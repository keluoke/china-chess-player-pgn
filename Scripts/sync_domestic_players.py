#!/usr/bin/env python3
"""Build domestic provisional player registry from event sightings.

This layer covers Chinese players who appear in domestic youth/master events
without a FIDE ID yet. Sightings are immutable evidence rows. Identity links can
later attach a sighting/domestic ID to a FIDE ID without rewriting history.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import pathlib
import re
from dataclasses import dataclass, field
from collections import Counter
from typing import Any

from apply_aliases_to_registry import sanitize_person_name
from stable_json import write_json as write_stable_json


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SIGHTINGS_CSV = REPO_ROOT / "data" / "manual" / "domestic-player-sightings.csv"
OBSERVATIONS_CSV = REPO_ROOT / "data" / "generated" / "person-observations.csv"
LINKS_CSV = REPO_ROOT / "data" / "manual" / "player-identity-links.csv"
OUTPUT_ROOT = REPO_ROOT / "docs" / "data" / "registry" / "domestic"
FIDE_REGISTRY = REPO_ROOT / "docs" / "data" / "registry" / "players.json"
PLAYER_EVENTS_CSV = REPO_ROOT / "data" / "generated" / "chess-results-player-events.csv"
PLAYER_NAME_MAP_CSV = REPO_ROOT / "data" / "generated" / "chess-results-player-name-map.csv"
PROMOTION_REVIEW = REPO_ROOT / "data" / "generated" / "audit" / "promotion-review.json"
PRESENTATION_NAMES = REPO_ROOT / "docs" / "data" / "identity" / "presentation-names.json"
PUBLIC_EVENT_DETAILS = REPO_ROOT / "docs" / "data" / "index" / "event-details"
FORBIDDEN_PRESENTATION_NAMES = {
    "8602980": {"居文君"},
    "8608288": {"徐翔宇"},
}


def validate_observation_manifest(path: pathlib.Path) -> None:
    meta_path = path.with_name(f"{path.stem}.meta.json")
    if not path.exists():
        return
    if not meta_path.is_file():
        raise ValueError(f"{path} has no schema/hash manifest: {meta_path}")
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid observation manifest: {meta_path}") from exc
    if meta.get("schemaVersion") != 2:
        raise ValueError(f"unsupported observation schemaVersion: {meta.get('schemaVersion')}")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != clean(meta.get("sha256")):
        raise ValueError("person-observations.csv does not match its manifest sha256")


@dataclass
class Sighting:
    sighting_id: str
    source: str
    event_id: str
    event_name: str
    event_date: str
    group: str
    age_stage: str
    player_name: str
    chinese_name: str
    pinyin_name: str
    sex: str
    birth_year: int | None
    province: str
    club: str
    rank: str
    score: str
    source_player_no: str
    source_url: str
    notes: str
    rounds: str = ""
    federation: str = ""
    event_scope: str = ""

    def payload(self) -> dict[str, Any]:
        return without_empty(
            {
                "sightingID": self.sighting_id,
                # De-sourcing contract: provider identity and links stay in
                # data/manual/; the public sighting only carries event facts.
                "eventID": self.event_id,
                "eventName": self.event_name,
                "eventDate": self.event_date,
                "group": self.group,
                "ageStage": self.age_stage,
                "playerName": self.player_name,
                "chineseName": self.chinese_name,
                "pinyin": self.pinyin_name,
                "federation": self.federation or None,
                "sex": self.sex,
                "birthYear": self.birth_year,
                # Public payload: never expose the raw club/school string for
                # minors. Only province/city-level `publicLocation` leaves the
                # public data surface; the full club stays in data/manual/.
                "publicLocation": location_from_text(" ".join(filter(None, (self.province, self.club)))),
                "rank": self.rank,
                "score": self.score,
                "rounds": self.rounds,
                "playerNo": self.source_player_no,
            }
        )


@dataclass
class IdentityLink:
    from_type: str
    from_id: str
    to_type: str
    to_id: str
    confidence: str
    evidence: str
    source_url: str
    reviewed_by: str
    reviewed_at: str
    notes: str

    def payload(self) -> dict[str, Any]:
        return without_empty(
            {
                "fromType": self.from_type,
                "fromID": self.from_id,
                "toType": self.to_type,
                "toID": self.to_id,
                "confidence": self.confidence,
                "reviewedBy": self.reviewed_by,
                "reviewedAt": self.reviewed_at,
                "notes": self.notes,
            }
        )


@dataclass
class DomesticPlayer:
    domestic_id: str
    canonical_id: str
    identity_status: str
    fide_id: str = ""
    chinese_name: str = ""
    pinyin_name: str = ""
    display_name: str = ""
    sex: str = ""
    birth_year: int | None = None
    province: str = ""
    club: str = ""
    aliases: list[str] = field(default_factory=list)
    sightings: list[Sighting] = field(default_factory=list)
    confidence: dict[str, Any] = field(default_factory=dict)
    public_status: str = "pending"

    def payload(self) -> dict[str, Any]:
        confirmed_chn = bool(self.fide_id) or any(
            str(s.federation or "").strip().upper() == "CHN"
            for s in self.sightings
        )
        fed = "CHN" if confirmed_chn else "unknown"
        basis = None if confirmed_chn else "domestic-event"
        return without_empty(
            {
                "id": self.canonical_id,
                "domesticID": self.domestic_id,
                "fideID": self.fide_id,
                "displayName": self.display_name,
                "chineseName": self.chinese_name,
                "pinyin": self.pinyin_name,
                "federation": fed,
                "domesticEligibilityBasis": basis,
                "sex": self.sex,
                "birthYear": self.birth_year,
                "publicLocation": public_location(self),
                "identityStatus": self.identity_status,
                "publicIdentityStatus": self.public_status,
                "entityType": "domestic-player",
                "aliases": ordered_unique(self.aliases),
                "sightingCount": len(self.sightings),
                "confidence": self.confidence,
                "sightings": [sighting.payload() for sighting in self.sightings],
            }
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Build domestic provisional player registry.")
    parser.add_argument("--sightings", type=pathlib.Path, default=SIGHTINGS_CSV)
    parser.add_argument("--observations", type=pathlib.Path, default=OBSERVATIONS_CSV)
    parser.add_argument("--links", type=pathlib.Path, default=LINKS_CSV)
    parser.add_argument("--output-root", type=pathlib.Path, default=OUTPUT_ROOT)
    parser.add_argument("--fide-registry", type=pathlib.Path, default=FIDE_REGISTRY)
    parser.add_argument("--player-events", type=pathlib.Path, default=PLAYER_EVENTS_CSV)
    parser.add_argument("--player-name-map", type=pathlib.Path, default=PLAYER_NAME_MAP_CSV)
    parser.add_argument("--promotion-review", type=pathlib.Path, default=PROMOTION_REVIEW)
    parser.add_argument("--event-details", type=pathlib.Path, default=PUBLIC_EVENT_DETAILS)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    sightings = read_sightings(args.sightings)
    validate_observation_manifest(args.observations)
    observation_stats = merge_observations(
        sightings,
        read_sightings(args.observations, required_columns={"federation", "event_scope"}),
    )
    links = read_links(args.links)
    players = build_players(sightings, links)
    assess_identity_confidence(players)
    name_groups = build_identity_name_groups(players)
    identity_candidates, conflict_edges = build_identity_candidates(players)
    chinese_name_candidates = build_chinese_name_candidates(
        args.fide_registry, args.player_events, args.player_name_map,
    )
    fide_candidates = build_fide_candidates(
        players,
        args.fide_registry,
        args.player_events,
        chinese_name_candidates,
        args.promotion_review,
        args.event_details,
    )
    presentation_groups = build_presentation_groups(
        players, identity_candidates, conflict_edges, fide_candidates,
    )
    apply_presentation_confidence(players, presentation_groups)
    placeholder_groups = [group for group in presentation_groups if clean(group.get("displayName")) == "姓名待核验"]
    if placeholder_groups:
        raise SystemExit(f"placeholder identity entered presentation groups: {len(placeholder_groups)}")
    write_output(
        players, sightings, links, name_groups, identity_candidates,
        fide_candidates, chinese_name_candidates, args.output_root,
        args.dry_run, conflict_edges, presentation_groups, args.fide_registry,
    )

    stats = {
        "sightings": len(sightings),
        "observationEnriched": observation_stats["enriched"],
        "observationAppended": observation_stats["appended"],
        "identityLinks": len(links),
        "domesticPlayers": len(players),
        "linkedToFIDE": sum(1 for player in players if player.fide_id),
        "unlinked": sum(1 for player in players if not player.fide_id),
        **identity_confidence_totals(players),
        "sameNameConflicts": sum(1 for player in players if player.confidence.get("sameNameConflictCount", 0)),
        "uniqueNameCount": len({identity_name(player) for player in players if identity_name(player)}),
        "sameNameGroups": len(name_groups),
        "fideLinkCandidates": len(fide_candidates),
        "highPriorityFideLinkCandidates": sum(candidate.get("queueTier") == "suggested-high" for candidate in fide_candidates),
        "chineseNameCandidates": len(chinese_name_candidates),
        "highPriorityChineseNameCandidates": sum(candidate.get("queueTier") == "suggested-high" for candidate in chinese_name_candidates),
        "conflictingChineseNameCandidates": sum(candidate.get("queueTier") == "conflict" for candidate in chinese_name_candidates),
        "identityCandidates": len(identity_candidates),
        "highPriorityIdentityCandidates": sum(candidate.get("queueTier") == "suggested-high" for candidate in identity_candidates),
        "presentationGroups": len(presentation_groups),
        "presentationGroupedEntities": sum(len(g["members"]) for g in presentation_groups),
        "parentOnlyNameGroups": sum(group.get("adjudicationMode") == "parent-only" for group in name_groups),
    }
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


def normalized_person_key(sighting: Sighting) -> str:
    return re.sub(r"\s+", "", (sighting.chinese_name or sighting.player_name or "")).casefold()


def merge_observations(sightings: list[Sighting], observations: list[Sighting]) -> dict[str, int]:
    """Fold machine PersonObservations into the sighting stream (plan P1-1).

    - A manual/legacy sighting for the same (event, playerNo, person) keeps
      its sighting_id — domestic deep links derive from it and must never
      break — but gains the participation facts (rank/score/club/date) the
      legacy startlist capture lacked.
    - Observations for rows the manual layer has never seen are appended as
      ordinary sightings. No identities are merged here.
    """
    index: dict[tuple[str, str], Sighting] = {}
    for sighting in sightings:
        key = (sighting.event_id, sighting.source_player_no)
        if all(key):
            index.setdefault(key, sighting)
    enriched = appended = 0
    for observation in observations:
        key = (observation.event_id, observation.source_player_no)
        existing = index.get(key) if all(key) else None
        if existing is not None:
            existing_person = normalized_person_key(existing)
            observed_person = normalized_person_key(observation)
            if existing_person and observed_person and existing_person != observed_person:
                # Roster row now names a different person: keep both rows as
                # separate evidence; adjudication belongs to the manual layer.
                sightings.append(observation)
                appended += 1
                continue
            existing.rank = existing.rank or observation.rank
            existing.score = existing.score or observation.score
            existing.rounds = existing.rounds or observation.rounds
            existing.club = existing.club or observation.club
            existing.event_name = existing.event_name or observation.event_name
            existing.event_date = existing.event_date or observation.event_date
            existing.age_stage = existing.age_stage or observation.age_stage
            existing.sex = existing.sex or observation.sex
            existing.federation = existing.federation or observation.federation
            existing.event_scope = existing.event_scope or observation.event_scope
            enriched += 1
        else:
            sightings.append(observation)
            appended += 1
    return {"enriched": enriched, "appended": appended}


def read_sightings(path: pathlib.Path, required_columns: set[str] | None = None) -> list[Sighting]:
    if not path.exists():
        return []
    sightings: list[Sighting] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = sorted((required_columns or set()) - set(reader.fieldnames or []))
        if missing:
            raise ValueError(f"{path} uses an incompatible schema; missing columns: {', '.join(missing)}")
        for row in reader:
            if not any((value or "").strip() for value in row.values()):
                continue
            sighting = Sighting(
                sighting_id=clean(row.get("sighting_id")) or generated_sighting_id(row),
                source=clean(row.get("source")),
                event_id=clean(row.get("event_id")),
                event_name=clean(row.get("event_name")),
                event_date=clean(row.get("event_date")),
                group=clean(row.get("group")),
                age_stage=clean(row.get("age_stage")),
                player_name=clean(row.get("player_name")),
                chinese_name=clean(row.get("chinese_name")),
                pinyin_name=clean(row.get("pinyin_name")),
                federation=clean(row.get("federation")),
                sex=clean(row.get("sex")),
                birth_year=parse_int(row.get("birth_year")),
                province=clean(row.get("province")),
                club=clean(row.get("club")),
                rank=clean(row.get("rank")),
                score=clean(row.get("score")),
                source_player_no=clean(row.get("source_player_no")),
                source_url=clean(row.get("source_url")),
                notes=clean(row.get("notes")),
                rounds=clean(row.get("rounds")),
                event_scope=clean(row.get("event_scope")),
            )
            sightings.append(sighting)
    return sightings


def read_links(path: pathlib.Path) -> list[IdentityLink]:
    if not path.exists():
        return []
    links: list[IdentityLink] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if not clean(row.get("from_id")) or not clean(row.get("to_id")):
                continue
            links.append(
                IdentityLink(
                    from_type=clean(row.get("from_type")).lower(),
                    from_id=clean(row.get("from_id")),
                    to_type=clean(row.get("to_type")).lower(),
                    to_id=clean(row.get("to_id")),
                    confidence=clean(row.get("confidence")),
                    evidence=clean(row.get("evidence")),
                    source_url=clean(row.get("source_url")),
                    reviewed_by=clean(row.get("reviewed_by")),
                    reviewed_at=clean(row.get("reviewed_at")),
                    notes=clean(row.get("notes")),
                )
            )
    return links


_international_events_cache = {}


def is_international_event(event_id: str) -> bool:
    if event_id in _international_events_cache:
        return _international_events_cache[event_id]

    tnr_id = event_id.removeprefix("chess-results-tnr").removeprefix("chess-results-").removeprefix("tnr")
    if not tnr_id.isdigit():
        _international_events_cache[event_id] = False
        return False

    json_path = REPO_ROOT / "data" / "generated" / "chess-results-event-details" / f"tnr{tnr_id}.json"
    if not json_path.exists():
        _international_events_cache[event_id] = False
        return False

    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
        for p in data.get("players", []):
            fed = str(p.get("federation") or "").strip().upper()
            if fed and fed not in {"CHN", "FIDE", "FID", "???", ""}:
                _international_events_cache[event_id] = True
                return True
    except Exception:
        pass

    _international_events_cache[event_id] = False
    return False


def build_players(sightings: list[Sighting], links: list[IdentityLink]) -> list[DomesticPlayer]:
    direct_sighting_links = {
        link.from_id: link
        for link in links
        if link.from_type == "sighting" and link.to_type in {"fide", "domestic"}
    }
    domestic_to_fide = {
        link.from_id: link.to_id
        for link in links
        if link.from_type == "domestic" and link.to_type == "fide"
    }

    grouped: dict[str, DomesticPlayer] = {}
    for sighting in sightings:
        link = direct_sighting_links.get(sighting.sighting_id)
        if link and link.to_type == "fide":
            domestic_id = provisional_domestic_id(sighting)
            fide_id = link.to_id
            canonical_id = f"fide-{fide_id}"
            status = "linked-fide"
        elif link and link.to_type == "domestic":
            domestic_id = link.to_id
            fide_id = domestic_to_fide.get(domestic_id, "")
            canonical_id = f"fide-{fide_id}" if fide_id else domestic_id
            status = "linked-fide" if fide_id else "domestic-linked"
        else:
            domestic_id = provisional_domestic_id(sighting)
            fide_id = domestic_to_fide.get(domestic_id, "")
            canonical_id = f"fide-{fide_id}" if fide_id else domestic_id
            status = "linked-fide" if fide_id else "unlinked"

        # P0 资格过滤：过滤明确为外籍或国际赛事中不具备中国身份的人员观察
        fed = str(sighting.federation or "").strip().upper()
        if fed and fed not in {"CHN", "FIDE", "FID", "???"}:
            continue
        international = sighting.event_scope == "international" or is_international_event(sighting.event_id)
        if international:
            if not (fed == "CHN" or link is not None or fide_id):
                continue

        player = grouped.get(canonical_id)
        if player is None:
            player = DomesticPlayer(
                domestic_id=domestic_id,
                canonical_id=canonical_id,
                identity_status=status,
                fide_id=fide_id,
            )
            grouped[canonical_id] = player

        apply_sighting(player, sighting)

    return sorted(grouped.values(), key=lambda player: (player.identity_status, player.display_name, player.domestic_id))


def apply_sighting(player: DomesticPlayer, sighting: Sighting) -> None:
    player.sightings.append(sighting)
    chinese_name = sanitize_person_name(sighting.chinese_name) or sanitize_person_name(sighting.player_name)
    player.chinese_name = player.chinese_name or chinese_name
    player.pinyin_name = player.pinyin_name or sighting.pinyin_name

    # A mixed/noisy Chinese cell is evidence, not an identity label.  Keep it
    # out of every public name/alias field instead of replacing it with a
    # second synthetic identity such as "姓名待核验".
    has_chinese = bool(re.search(r"[\u4e00-\u9fff]", sighting.player_name))
    latin_name = "" if has_chinese else sighting.player_name
    player.display_name = player.display_name or chinese_name or latin_name or sighting.pinyin_name or player.domestic_id

    player.sex = player.sex or sighting.sex
    player.birth_year = player.birth_year or sighting.birth_year
    player.province = player.province or sighting.province
    player.club = player.club or sighting.club
    player.aliases.extend(
        [
            player.domestic_id,
            player.fide_id,
            latin_name,
            chinese_name,
            sighting.pinyin_name,
            sighting.pinyin_name.replace(" ", ""),
        ]
    )


def assess_identity_confidence(players: list[DomesticPlayer]) -> None:
    """Attach review signals without ever auto-merging two sightings.

    A score helps reviewers order work; it is deliberately not an identity
    decision. Only rows in player-identity-links.csv may merge sightings or
    connect a domestic entity to the FIDE registry.
    """
    name_counts = Counter(identity_name(player) for player in players if identity_name(player))
    for player in players:
        event_count = len({s.event_id or s.source_url for s in player.sightings if s.event_id or s.source_url})
        duplicate_total = name_counts.get(identity_name(player), 1)
        same_name_conflicts = max(0, duplicate_total - 1)
        continuity = age_stage_continuity(player.sightings)
        birth_years = {s.birth_year for s in player.sightings if s.birth_year is not None}

        score = 0
        reasons: list[str] = []
        weights: dict[str, int] = {}
        if player.identity_status == "domestic-linked":
            score = 90
            reasons = ["已有人工复核的国内身份关联"]
        elif player.fide_id:
            score = 100
            reasons = ["已有人工复核的 FIDE 身份关联"]
        if event_count > 1:
            weights["crossEvent"] = 30
            score += 30
            reasons.append(f"跨 {event_count} 项赛事出现")
        clubs = {normalize_club(s.club) for s in player.sightings if normalize_club(s.club)}
        if len(player.sightings) > 1 and len(clubs) == 1:
            weights["clubConsistency"] = 20
            score += 20
            reasons.append("跨赛事参赛单位一致")
        if continuity == "consistent":
            weights["ageContinuity"] = 15
            score += 15
            reasons.append("年龄段随时间连续")
        elif continuity == "conflict":
            weights["ageConflict"] = -30
            score -= 30
            reasons.append("年龄段时间线存在冲突")
        if duplicate_total == 1:
            weights["uniqueName"] = 25
            score += 25
            reasons.append("全库姓名唯一")
        if len(birth_years) > 1:
            weights["birthYearConflict"] = -40
            score -= 40
            reasons.append("出生年证据冲突")
        if duplicate_total >= 3:
            reasons.append(f"同名簇共 {duplicate_total} 条，仅允许人工区分")

        score = max(0, min(100, score))
        player.public_status = "verified" if player.fide_id or player.identity_status == "domestic-linked" else "same-name" if duplicate_total > 1 else "pending"
        player.confidence = {
            "score": score,
            "level": "high" if score >= 85 else "medium" if score >= 70 else "low",
            "weights": weights,
            "sameNameConflictCount": same_name_conflicts,
            "sameNameClusterSize": duplicate_total,
            "crossEventCount": event_count,
            "ageStageContinuity": continuity,
            "reviewRequired": not (player.fide_id or player.identity_status == "domestic-linked"),
            "machineNominationAllowed": duplicate_total < 3,
            "reasons": reasons or ["单条赛事观察，等待更多可区分证据"],
        }


def identity_confidence_totals(players: list[DomesticPlayer]) -> dict[str, int]:
    levels = Counter(clean(player.confidence.get("level")).lower() for player in players)
    return {
        "highConfidence": levels.get("high", 0),
        "mediumConfidence": levels.get("medium", 0),
        "lowConfidence": levels.get("low", 0),
        "reviewRequired": sum(
            bool(player.confidence.get("reviewRequired")) for player in players
        ),
    }


def apply_presentation_confidence(
    players: list[DomesticPlayer],
    groups: list[dict[str, Any]],
) -> None:
    """Project evidence-backed group confidence onto public domestic cards.

    This does not merge sightings or mutate registry/manual data. It only
    prevents the derived player summaries from continuing to call every
    machine-grouped member ``low`` after the stricter presentation graph has
    already proved a high-confidence, conflict-free component.
    """
    by_id = {player.domestic_id: player for player in players}
    for group in groups:
        if group.get("confidenceTier") != "high":
            continue
        basis = clean(group.get("identityBasis"))
        for domestic_id in group.get("members") or []:
            player = by_id.get(clean(domestic_id))
            if player is None or player.fide_id:
                continue
            confidence = dict(player.confidence)
            weights = dict(confidence.get("weights") or {})
            weights["presentationEvidence"] = max(
                90, int(weights.get("presentationEvidence") or 0)
            )
            reasons = list(confidence.get("reasons") or [])
            reason = f"成员级证据通过 {basis or 'presentation-high'} 展示归组"
            if reason not in reasons:
                reasons.append(reason)
            confidence.update({
                "score": max(90, int(confidence.get("score") or 0)),
                "level": "high",
                "weights": weights,
                # A display projection remains disputable until a reviewed
                # player-identity-links.csv row makes it permanent.
                "reviewRequired": True,
                "presentationGroupID": clean(group.get("groupID")),
                "presentationRuleVersion": IDENTITY_CLUSTER_RULE_VERSION,
                "reasons": reasons,
            })
            player.confidence = confidence
            player.public_status = "presentation-high"


def identity_name(player: DomesticPlayer) -> str:
    name = player.chinese_name or player.pinyin_name or player.display_name
    if not name or name == player.domestic_id or name == "姓名待核验":
        return ""
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", clean(name).casefold())


def identity_keys(player: DomesticPlayer) -> list[str]:
    """Return every usable person-name key without changing registry data."""
    ignored = {
        normalized_name(player.domestic_id),
        normalized_name(player.canonical_id),
        normalized_name(player.fide_id),
        normalized_name("姓名待核验"),
    }
    result: list[str] = []
    for value in (
        player.chinese_name,
        player.pinyin_name,
        player.display_name,
        *player.aliases,
    ):
        key = normalized_name(value)
        if key and key not in ignored and key not in result:
            result.append(key)
    return result


def normalize_club(value: Any) -> str:
    return re.sub(r"(?:有限责任公司|有限公司|体育文化|教育咨询|国际象棋|棋类|俱乐部|学校|协会|中心)", "", clean(value).casefold()).strip()


GENERIC_CLUBS = {
    "", "0", "a2", "b3", "china", "chn", "open", "中国",
    "北京市", "上海市", "天津市", "重庆市", "福建省", "广东省",
    "河北省", "河南省", "江苏省", "四川省", "浙江省",
}


def distinctive_club(value: Any) -> str:
    club = normalize_club(value)
    return club if len(club) >= 4 and club not in GENERIC_CLUBS else ""


def age_stage_continuity(sightings: list[Sighting]) -> str:
    observations: list[tuple[str, int]] = []
    for sighting in sightings:
        match = re.fullmatch(r"U\s*(\d{1,2})", sighting.age_stage, flags=re.IGNORECASE)
        year_match = re.match(r"(\d{4})", sighting.event_date)
        if match and year_match:
            observations.append((sighting.event_date, int(match.group(1))))
    if len(observations) < 2:
        return "unknown"
    stages = [stage for _, stage in sorted(observations)]
    return "consistent" if all(current >= previous for previous, current in zip(stages, stages[1:])) else "conflict"


def build_identity_name_groups(players: list[DomesticPlayer]) -> list[dict[str, Any]]:
    grouped: dict[str, list[DomesticPlayer]] = {}
    for player in players:
        key = identity_name(player)
        if key:
            grouped.setdefault(key, []).append(player)
    result: list[dict[str, Any]] = []
    for key, members in grouped.items():
        if len(members) < 2:
            continue
        parent_only = len(members) >= 3
        result.append({
            "normalizedName": key,
            "displayName": members[0].display_name,
            "provisionalEntityCount": len(members),
            "domesticIDs": [member.domestic_id for member in members],
            "eventIDs": sorted({s.event_id for member in members for s in member.sightings if s.event_id}),
            "clubs": sorted({s.club for member in members for s in member.sightings if s.club}),
            "ageStages": sorted({s.age_stage for member in members for s in member.sightings if s.age_stage}),
            "reviewRequired": True,
            "adjudicationMode": "parent-only" if parent_only else "review-candidate",
            "machineNominationAllowed": not parent_only,
            "warning": "同名簇达到 3 条，机器不生成合并提名" if parent_only else "同名观察不得自动合并，候选仅供人工复核",
        })
    result.sort(key=lambda row: (-row["provisionalEntityCount"], row["displayName"]))
    return result


# Explicit domestic ladder (plan §5.1): promotion evidence only travels along
# adjacent levels of this graph, never across arbitrary group names.
GROUP_LEVELS = (
    ("四级棋士", 1), ("三级棋士", 2), ("二级棋士", 3), ("一级棋士", 4),
    ("候补", 5), ("公开", 6), ("棋协大师", 6),
)
PROMOTION_SCORE_RATE = 0.65
PROMOTION_WINDOW_DAYS = 730
CANDIDATE_ALGORITHM_VERSION = "identity-candidates-v4"
IDENTITY_CLUSTER_RULE_VERSION = "identity-presentation-v4"


def group_level(sighting: Sighting) -> int:
    title = f"{sighting.group} {sighting.event_name}"
    for keyword, level in GROUP_LEVELS:
        if keyword in title:
            return level
    return 0


def score_rate(sighting: Sighting) -> float | None:
    try:
        score = float(str(sighting.score).replace(",", "."))
        rounds = float(sighting.rounds)
    except (TypeError, ValueError):
        return None
    if rounds <= 0:
        return None
    return score / rounds


def parse_date(value: str) -> dt.date | None:
    text = str(value or "").strip()
    if re.fullmatch(r"\d{4}", text):
        return None
    try:
        return dt.date.fromisoformat(text[:10])
    except (TypeError, ValueError):
        return None


def exact_date(value: str) -> bool:
    return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(value or "").strip()[:10]))


def date_year(value: str) -> int | None:
    match = re.match(r"^(\d{4})", str(value or "").strip())
    return int(match.group(1)) if match else None


def promotion_evidence(left: DomesticPlayer, right: DomesticPlayer) -> dict[str, Any] | None:
    """≥65% score in a lower group followed (≤24 months) by an appearance in
    the adjacent higher group — the strongest weak-evidence signal.

    ``exactDates`` records whether the ordering rests on real event dates;
    year-only anchors keep the evidence for queue ordering but never trigger
    automatic display aggregation (review §4.2)."""
    best: dict[str, Any] | None = None
    for lower, higher in ((left, right), (right, left)):
        for low_sighting in lower.sightings:
            low_level = group_level(low_sighting)
            rate = score_rate(low_sighting)
            low_date = parse_date(low_sighting.event_date)
            low_year = date_year(low_sighting.event_date)
            if not low_level or rate is None or rate < PROMOTION_SCORE_RATE or low_year is None:
                continue
            for high_sighting in higher.sightings:
                high_level = group_level(high_sighting)
                high_date = parse_date(high_sighting.event_date)
                high_year = date_year(high_sighting.event_date)
                if high_level != low_level + 1 or high_year is None:
                    continue
                is_exact = bool(low_date and high_date)
                if is_exact:
                    gap = (high_date - low_date).days
                    ordered = 0 <= gap <= PROMOTION_WINDOW_DAYS
                    months = round(gap / 30)
                    precision = "exact"
                elif high_year > low_year:
                    gap = (high_year - low_year) * 365
                    ordered = gap <= PROMOTION_WINDOW_DAYS
                    months = (high_year - low_year) * 12
                    precision = "year-ordered"
                elif high_year == low_year:
                    ordered = False
                    months = None
                    precision = "same-year-unordered"
                else:
                    continue
                if ordered or precision == "same-year-unordered":
                    candidate = {
                        "lowerGroup": low_sighting.group,
                        "scoreRate": round(rate, 3),
                        "higherGroup": high_sighting.group,
                        "monthsBetween": months,
                        "exactDates": is_exact,
                        "ordered": ordered,
                        "datePrecision": precision,
                    }
                    if best is None or (candidate["ordered"] and not best["ordered"]) or (candidate["exactDates"] and not best["exactDates"]):
                        best = candidate
                    if best["exactDates"]:
                        return best
    return best


def competition_stage_key(sighting: Sighting) -> str:
    """Stable key for proving that two sightings stayed in the same section.

    Domestic title levels take precedence over broad age labels such as OPEN,
    otherwise two different master levels could be mistaken for one group.
    """
    title = f"{sighting.group} {sighting.event_name}"
    for keyword, _level in GROUP_LEVELS:
        if keyword in title:
            return f"ladder:{keyword}"
    group = normalized_name(sighting.group)
    if group:
        return f"group:{group}"
    age_stage = normalized_name(sighting.age_stage)
    return f"age:{age_stage}" if age_stage else ""


def sighting_event_key(sighting: Sighting) -> str:
    event_id = clean(sighting.event_id)
    if event_id:
        return event_id
    event_name = clean(sighting.event_name)
    event_date = clean(sighting.event_date)
    return f"{event_name}|{event_date}" if event_name and event_date else ""


def same_stage_nonpromotion_evidence(
    left: DomesticPlayer,
    right: DomesticPlayer,
) -> dict[str, Any] | None:
    """Prove same-person continuity when a player did not earn promotion.

    A display merge requires a distinctive club, the same normalized group,
    an earlier score below the promotion threshold, two distinct events and
    an ordered timeline within the normal promotion window. Year-only dates
    are accepted only across different years; same-year ordering needs exact
    dates. Hard conflicts are checked by the caller before this runs.
    """
    best: dict[str, Any] | None = None
    for earlier_player, later_player in ((left, right), (right, left)):
        for earlier in earlier_player.sightings:
            earlier_rate = score_rate(earlier)
            stage_key = competition_stage_key(earlier)
            club_key = distinctive_club(earlier.club)
            earlier_event = sighting_event_key(earlier)
            if (
                earlier_rate is None
                or earlier_rate >= PROMOTION_SCORE_RATE
                or not stage_key
                or not club_key
                or not earlier_event
            ):
                continue
            for later in later_player.sightings:
                later_event = sighting_event_key(later)
                if (
                    later_event == earlier_event
                    or distinctive_club(later.club) != club_key
                    or competition_stage_key(later) != stage_key
                ):
                    continue
                earlier_date = parse_date(earlier.event_date)
                later_date = parse_date(later.event_date)
                earlier_year = date_year(earlier.event_date)
                later_year = date_year(later.event_date)
                if earlier_date and later_date:
                    gap = (later_date - earlier_date).days
                    ordered = 0 < gap <= PROMOTION_WINDOW_DAYS
                    months = round(gap / 30)
                    precision = "exact"
                elif earlier_year and later_year and later_year > earlier_year:
                    gap = (later_year - earlier_year) * 365
                    ordered = gap <= PROMOTION_WINDOW_DAYS
                    months = (later_year - earlier_year) * 12
                    precision = "year-ordered"
                else:
                    ordered = False
                    months = None
                    precision = "same-year-unordered"
                if not ordered:
                    continue
                candidate = {
                    "group": earlier.group or earlier.age_stage,
                    "scoreRate": round(earlier_rate, 3),
                    "monthsBetween": months,
                    "exactDates": bool(earlier_date and later_date),
                    "ordered": True,
                    "datePrecision": precision,
                    "eventIDs": [earlier_event, later_event],
                }
                if best is None or (candidate["exactDates"] and not best["exactDates"]):
                    best = candidate
                if candidate["exactDates"]:
                    return candidate
    return best


def sexes_consistent(left: DomesticPlayer, right: DomesticPlayer) -> bool:
    """True unless there is an explicit sex conflict (M vs F) between the sides."""
    left_sex = {s.sex for s in left.sightings if s.sex} or ({left.sex} if left.sex else set())
    right_sex = {s.sex for s in right.sightings if s.sex} or ({right.sex} if right.sex else set())
    if "M" in left_sex and "F" in right_sex:
        return False
    if "F" in left_sex and "M" in right_sex:
        return False
    return True


def hard_conflicts(left: DomesticPlayer, right: DomesticPlayer) -> list[str]:
    conflicts: list[str] = []
    left_events = {s.event_id for s in left.sightings if s.event_id}
    right_events = {s.event_id for s in right.sightings if s.event_id}
    shared = left_events & right_events
    if shared:
        # Same person cannot hold two roster slots in one section capture.
        conflicts.append(f"concurrent-event:{sorted(shared)[0]}")
    left_birth = {s.birth_year for s in left.sightings if s.birth_year}
    right_birth = {s.birth_year for s in right.sightings if s.birth_year}
    if left_birth and right_birth and left_birth.isdisjoint(right_birth):
        conflicts.append("birth-year-conflict")
    if age_stage_continuity([*left.sightings, *right.sightings]) == "conflict":
        conflicts.append("age-stage-conflict")
    return conflicts


def pair_candidate(key: str, left: DomesticPlayer, right: DomesticPlayer,
                   cluster_size: int) -> dict[str, Any] | None:
    """Evidence card for one same-name pair; None when a hard conflict bans
    the edge. Weights follow plan §5.1 and are reviewed product parameters,
    not identity facts."""
    conflicts = hard_conflicts(left, right)
    if conflicts:
        return {"_conflict": True, "domesticIDs": [left.domestic_id, right.domestic_id],
                "normalizedName": key, "reasons": conflicts}

    weights: dict[str, int] = {}
    evidence_kinds = 0
    summary: list[str] = []

    left_clubs = {normalize_club(s.club) for s in left.sightings if normalize_club(s.club)}
    right_clubs = {normalize_club(s.club) for s in right.sightings if normalize_club(s.club)}
    shared_distinctive_clubs = {
        distinctive_club(s.club) for s in left.sightings if distinctive_club(s.club)
    } & {
        distinctive_club(s.club) for s in right.sightings if distinctive_club(s.club)
    }
    if left_clubs & right_clubs:
        weights["clubConsistency"] = 25
        evidence_kinds += 1
        summary.append("俱乐部一致")

    promotion = promotion_evidence(left, right)
    if promotion:
        weights["promotionPattern"] = 35
        evidence_kinds += 1
        if promotion.get("ordered"):
            desc = (
                f"{promotion['lowerGroup']} 得分率 {promotion['scoreRate']:.0%} → "
                f"{promotion['monthsBetween']} 个月后出现在 {promotion['higherGroup']}"
            )
        else:
            desc = (
                f"同年在 {promotion['lowerGroup']} 得分率 {promotion['scoreRate']:.0%} 且出现于 "
                f"{promotion['higherGroup']}，先后顺序待精确日期核验"
            )
        summary.append(desc)

    same_stage = same_stage_nonpromotion_evidence(left, right)
    if same_stage:
        weights["sameStageAfterNonPromotion"] = 35
        evidence_kinds += 1
        summary.append(
            f"{same_stage['group']} 得分率 {same_stage['scoreRate']:.0%} 未达晋级线，"
            f"{same_stage['monthsBetween']} 个月后仍在同组"
        )

    combined_continuity = age_stage_continuity([*left.sightings, *right.sightings])
    if combined_continuity == "consistent":
        weights["ageContinuity"] = 15
        evidence_kinds += 1
        summary.append("年龄组连续")
    elif combined_continuity == "conflict":
        weights["ageConflict"] = -30

    left_birth = {s.birth_year for s in left.sightings if s.birth_year}
    right_birth = {s.birth_year for s in right.sightings if s.birth_year}
    if left_birth and right_birth and left_birth == right_birth:
        weights["birthYearMatch"] = 20
        evidence_kinds += 1
        summary.append("出生年一致")

    left_regions = {location_from_text(f"{s.province} {s.club}") for s in left.sightings}
    right_regions = {location_from_text(f"{s.province} {s.club}") for s in right.sightings}
    if (left_regions & right_regions) - {""}:
        weights["publicRegionMatch"] = 10
        evidence_kinds += 1
        summary.append("公开地区一致")

    score = max(0, min(100, sum(weights.values())))
    # Review §4.2: same-club continuity supports two symmetric paths:
    # a real ordered promotion, or a below-threshold result followed by an
    # ordered appearance in the same group. The latter requires a distinctive
    # club because generic province/team labels are not identity evidence.
    ordered_promotion = bool(promotion and promotion.get("ordered"))
    club_plus_promotion = bool(weights.get("clubConsistency")) and ordered_promotion and sexes_consistent(left, right)
    club_plus_same_stage = bool(
        shared_distinctive_clubs
        and same_stage
        and same_stage.get("ordered")
        and sexes_consistent(left, right)
    )
    if club_plus_promotion or club_plus_same_stage or (score >= 70 and evidence_kinds >= 2):
        tier = "suggested-high"
    elif score >= 45:
        tier = "suggested-medium"
    else:
        tier = "low"
    presentation_eligible = bool(club_plus_promotion or club_plus_same_stage)
    presentation_basis = (
        "distinctive-club+same-stage-after-nonpromotion"
        if club_plus_same_stage
        else "club+ordered-promotion"
        if club_plus_promotion
        else ""
    )
    return {
        "presentationEligible": presentation_eligible,
        "presentationBasis": presentation_basis,
        "candidateID": "identity-candidate-" + hashlib.sha256(f"{left.domestic_id}|{right.domestic_id}".encode("utf-8")).hexdigest()[:16],
        "algorithmVersion": CANDIDATE_ALGORITHM_VERSION,
        "normalizedName": key,
        "displayName": left.display_name,
        "domesticIDs": [left.domestic_id, right.domestic_id],
        "clusterSize": cluster_size,
        "score": score,
        "queueTier": tier,
        "weights": weights,
        "evidenceKinds": evidence_kinds,
        "evidenceSummary": "；".join(summary) or "仅同名，无独立证据",
        "eventIDs": sorted({s.event_id for p in (left, right) for s in p.sightings if s.event_id}),
        "clubs": sorted({s.club for p in (left, right) for s in p.sightings if s.club}),
        "ageStageContinuity": combined_continuity,
        "machineNominationAllowed": True,
        "reviewRequired": True,
        "warning": "机器仅提名候选，禁止自动写入 player-identity-links.csv",
    }


def build_identity_candidates(players: list[DomesticPlayer]) -> list[dict[str, Any]]:
    """Pairwise same-name candidate edges with hard-conflict pruning.

    Clusters of 3+ used to be silently skipped ("parent-only"); the plan
    replaces that with an explicit pairwise graph so maintainers see ranked,
    mutually exclusive merge options — still never an automatic merge."""
    grouped: dict[str, list[DomesticPlayer]] = {}
    for player in players:
        key = identity_name(player)
        if key:
            grouped.setdefault(key, []).append(player)
    candidates: list[dict[str, Any]] = []
    conflict_edges: list[dict[str, Any]] = []
    for key, members in grouped.items():
        if len(members) < 2:
            continue
        members = sorted(members, key=lambda p: p.domestic_id)
        if len(members) > 8:
            # Very common names: only nominate pairs sharing a club to keep
            # the queue reviewable; everything else stays folded.
            pairs = [
                (a, b) for i, a in enumerate(members) for b in members[i + 1:]
                if {normalize_club(s.club) for s in a.sightings if normalize_club(s.club)}
                & {normalize_club(s.club) for s in b.sightings if normalize_club(s.club)}
            ]
        else:
            pairs = [(a, b) for i, a in enumerate(members) for b in members[i + 1:]]
        for left, right in pairs:
            card = pair_candidate(key, left, right, len(members))
            if card is None:
                continue
            if card.get("_conflict"):
                card.pop("_conflict")
                conflict_edges.append(card)
            elif card["score"] > 0:
                candidates.append(card)
    candidates.sort(key=lambda row: (-row["score"], row["displayName"], row["candidateID"]))
    return candidates, conflict_edges


# --- presentation identity groups (review §4) --------------------------------

DISPUTES_CSV = REPO_ROOT / "data" / "manual" / "presentation-disputes.csv"


def pair_hash(a: str, b: str) -> str:
    left, right = sorted([a, b])
    return hashlib.sha256(f"{left}|{right}".encode("utf-8")).hexdigest()[:16]


def load_presentation_disputes() -> dict[str, str]:
    """pair_hash -> status. Statuses that block regrouping: disputed,
    confirmed-separate, tombstone. ``confirmed-merged`` is a maintainer
    decision that belongs in player-identity-links.csv, not here."""
    blocked: dict[str, str] = {}
    if not DISPUTES_CSV.exists():
        return blocked
    with DISPUTES_CSV.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            status = clean(row.get("status")).lower()
            hash_value = clean(row.get("pair_hash"))
            if not hash_value:
                a, b = clean(row.get("member_a")), clean(row.get("member_b"))
                if a and b:
                    hash_value = pair_hash(a, b)
            if hash_value and status in ("disputed", "confirmed-separate", "tombstone"):
                blocked[hash_value] = status
    return blocked


def build_presentation_groups(
    players: list[DomesticPlayer],
    identity_candidates: list[dict[str, Any]],
    conflict_edges: list[dict[str, Any]],
    fide_candidates: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """High-confidence display-only aggregation (review §4.1–4.5).

    - Edges: presentationEligible candidate pairs (same club + ordered
      promotion, or distinctive club + ordered same-group continuation after
      a below-threshold result; always consistent sex and no hard conflicts)
      minus disputed/tombstoned pairs.
    - Components form via union-find, but a component containing ANY internal
      hard-conflict or blocked pair is discarded entirely: transitive closure
      never bypasses a conflict edge (review §4.5).
    - Groups are pure projections: no Person/registry/observation mutation.
    """
    disputes = load_presentation_disputes()
    conflict_pairs = {
        pair_hash(*edge["domesticIDs"]) for edge in conflict_edges
        if len(edge.get("domesticIDs") or []) == 2
    }
    by_id = {player.domestic_id: player for player in players}
    groups: list[dict[str, Any]] = []
    fide_members: set[str] = set()
    reviewed_fide_ids: set[str] = set()

    # Reviewed domestic→FIDE links are permanent identity decisions, but
    # remain a projection on top of the authoritative FIDE registry.  Emit a
    # group so the frontend can attach the domestic event history and reviewed
    # Chinese display name to the existing FIDE card without duplicating it.
    for player in players:
        fide_id = clean(player.fide_id)
        if not fide_id or not player.sightings:
            continue
        reviewed_fide_ids.add(fide_id)
        fide_members.add(player.domestic_id)
        groups.append({
            "groupID": "pg-fide-reviewed-" + hashlib.sha256(
                f"{fide_id}|{player.domestic_id}".encode("utf-8")
            ).hexdigest()[:12],
            "canonicalFideID": fide_id,
            "members": [player.domestic_id],
            "memberRefs": [{
                "id": player.domestic_id,
                "shard": hashlib.sha256(player.domestic_id.encode("utf-8")).hexdigest()[:2],
            }],
            "disputeMembers": [f"fide-{fide_id}", player.domestic_id],
            "displayName": player.display_name,
            "suggestedChineseName": player.chinese_name,
            "sightingCount": len(player.sightings),
            "identityBasis": "reviewed-fide-link",
            "confidenceTier": "high",
            "ruleVersion": IDENTITY_CLUSTER_RULE_VERSION,
            "disputeEntry": True,
        })

    # A domestic observation may project onto a verified FIDE card without
    # becoming a permanent identity link.  This closes the gap where the
    # source already ties the name to a FIDE ID (or a distinctive club agrees)
    # but the reviewed player-identity-links.csv row does not exist yet.
    fide_groups: dict[str, list[dict[str, Any]]] = {}
    for candidate in fide_candidates or []:
        if not candidate.get("presentationEligible"):
            continue
        domestic_id = clean(candidate.get("domesticID"))
        fide_id = clean(candidate.get("candidateFideID"))
        if not domestic_id or not fide_id:
            continue
        if fide_id in reviewed_fide_ids:
            continue
        if pair_hash(f"fide-{fide_id}", domestic_id) in disputes:
            continue
        fide_groups.setdefault(fide_id, []).append(candidate)

    for fide_id, candidates in sorted(fide_groups.items()):
        member_list = sorted({clean(card.get("domesticID")) for card in candidates if clean(card.get("domesticID"))})
        internal_block = any(
            pair_hash(member_list[i], member_list[j]) in conflict_pairs
            or pair_hash(member_list[i], member_list[j]) in disputes
            for i in range(len(member_list)) for j in range(i + 1, len(member_list))
        )
        if internal_block or not member_list:
            continue
        fide_members.update(member_list)
        suggested_names = {
            clean(card.get("suggestedChineseName")) for card in candidates
            if clean(card.get("suggestedChineseName"))
        }
        display_name = clean(candidates[0].get("candidateFideName"))
        groups.append({
            "groupID": "pg-fide-" + hashlib.sha256(f"{fide_id}|{'|'.join(member_list)}".encode("utf-8")).hexdigest()[:12],
            "canonicalFideID": fide_id,
            "members": member_list,
            "memberRefs": [
                {"id": member, "shard": hashlib.sha256(member.encode("utf-8")).hexdigest()[:2]}
                for member in member_list
            ],
            "disputeMembers": [f"fide-{fide_id}", *member_list],
            "displayName": display_name,
            "suggestedChineseName": next(iter(suggested_names)) if len(suggested_names) == 1 else "",
            "sightingCount": sum(len(by_id[m].sightings) for m in member_list if m in by_id),
            "identityBasis": "presentation-high-fide",
            "confidenceTier": "high",
            "ruleVersion": IDENTITY_CLUSTER_RULE_VERSION,
            "disputeEntry": True,
        })

    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        parent[find(a)] = find(b)

    eligible_edges: list[tuple[str, str]] = []
    eligible_edge_basis: dict[str, str] = {}
    for candidate in identity_candidates:
        if not candidate.get("presentationEligible"):
            continue
        ids = candidate.get("domesticIDs") or []
        if len(ids) != 2:
            continue
        if ids[0] in fide_members or ids[1] in fide_members:
            continue
        hash_value = pair_hash(ids[0], ids[1])
        if hash_value in disputes or hash_value in conflict_pairs:
            continue
        eligible_edges.append((ids[0], ids[1]))
        eligible_edge_basis[hash_value] = clean(candidate.get("presentationBasis"))
        union(ids[0], ids[1])

    components: dict[str, set[str]] = {}
    for a, b in eligible_edges:
        components.setdefault(find(a), set()).update([a, b])

    for members in components.values():
        member_list = sorted(members)
        # Mutual-exclusion check: any internal blocked/conflict pair kills
        # the whole component — those members stay as separate cards.
        internal_block = any(
            pair_hash(member_list[i], member_list[j]) in conflict_pairs
            or pair_hash(member_list[i], member_list[j]) in disputes
            for i in range(len(member_list)) for j in range(i + 1, len(member_list))
        )
        if internal_block or len(member_list) < 2:
            continue
        uses_same_stage_evidence = any(
            eligible_edge_basis.get(pair_hash(member_list[i], member_list[j]))
            == "distinctive-club+same-stage-after-nonpromotion"
            for i in range(len(member_list)) for j in range(i + 1, len(member_list))
        )
        if uses_same_stage_evidence:
            shared_component_clubs: set[str] | None = None
            for member in member_list:
                member_clubs = {
                    distinctive_club(sighting.club)
                    for sighting in by_id[member].sightings
                    if distinctive_club(sighting.club)
                }
                shared_component_clubs = (
                    member_clubs
                    if shared_component_clubs is None
                    else shared_component_clubs & member_clubs
                )
            if not shared_component_clubs:
                continue
        primary = by_id.get(member_list[0])
        groups.append({
            "groupID": "pg-" + hashlib.sha256("|".join(member_list).encode("utf-8")).hexdigest()[:12],
            "members": member_list,
            # Shard prefixes let the frontend fetch every member's facts
            # without recomputing the hash layout client-side.
            "memberRefs": [
                {"id": member, "shard": hashlib.sha256(member.encode("utf-8")).hexdigest()[:2]}
                for member in member_list
            ],
            "displayName": primary.display_name if primary else "",
            "sex": primary.sex if primary else "",
            "sightingCount": sum(len(by_id[m].sightings) for m in member_list if m in by_id),
            "identityBasis": "presentation-high",
            "confidenceTier": "high",
            "ruleVersion": IDENTITY_CLUSTER_RULE_VERSION,
            "disputeEntry": True,
        })
    groups.sort(key=lambda group: group["groupID"])
    return groups


def normalized_name(value: Any) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", clean(value).casefold())


def read_player_event_evidence(path: pathlib.Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [
            {key: clean(value) for key, value in row.items()}
            for row in csv.DictReader(handle)
            if clean(row.get("fide_id")) and clean(row.get("tnrid"))
        ]


def build_chinese_name_candidates(
    registry_path: pathlib.Path,
    player_events_path: pathlib.Path = PLAYER_EVENTS_CSV,
    player_name_map_path: pathlib.Path = PLAYER_NAME_MAP_CSV,
) -> list[dict[str, Any]]:
    """Build reviewable FIDE->Chinese-name evidence without mutating registry."""
    if not registry_path.exists():
        return []
    registry = {
        clean(row.get("fideID")): row
        for row in json.loads(registry_path.read_text(encoding="utf-8"))
        if clean(row.get("fideID"))
    }
    evidence: dict[str, dict[str, set[str]]] = {}
    for row in read_player_event_evidence(player_events_path):
        fide_id = row["fide_id"]
        if fide_id not in registry or clean(registry[fide_id].get("chineseName")):
            continue
        chinese_name = sanitize_person_name(row.get("player_name"))
        if chinese_name:
            evidence.setdefault(fide_id, {}).setdefault(chinese_name, set()).add(row["tnrid"])

    # The name-map is an additional machine observation.  Only its dedicated
    # chinese_name cell is accepted; dirty name_variants never flow forward.
    if player_name_map_path.exists():
        with player_name_map_path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                fide_id = clean(row.get("fide_id"))
                if fide_id not in registry or clean(registry[fide_id].get("chineseName")):
                    continue
                chinese_name = sanitize_person_name(row.get("chinese_name"))
                if chinese_name:
                    event_id = clean(row.get("evidence_tnrid")) or "name-map"
                    evidence.setdefault(fide_id, {}).setdefault(chinese_name, set()).add(event_id)

    result: list[dict[str, Any]] = []
    for fide_id, names in evidence.items():
        event_ids = sorted({event_id for ids in names.values() for event_id in ids if event_id != "name-map"})
        conflict = len(names) > 1
        chinese_name = next(iter(names)) if len(names) == 1 else ""
        repeated = bool(chinese_name and len(names[chinese_name] - {"name-map"}) >= 2)
        tier = "conflict" if conflict else "suggested-high" if repeated else "suggested-medium"
        result.append({
            "candidateID": f"chinese-name-{fide_id}",
            "fideID": fide_id,
            "registryName": clean(registry[fide_id].get("displayName") or registry[fide_id].get("name")),
            "suggestedChineseName": chinese_name,
            "candidateNames": sorted(names),
            "eventCount": len(event_ids),
            "eventIDs": event_ids,
            "queueTier": tier,
            "presentationEligible": repeated and not conflict,
            "reviewRequired": True,
            "warning": "仅为展示/审核候选；接受后写入 player-aliases.csv，禁止直接覆盖 registry",
        })
    order = {"conflict": 0, "suggested-high": 1, "suggested-medium": 2}
    result.sort(key=lambda row: (order.get(row["queueTier"], 9), -row["eventCount"], row["fideID"]))
    return result


def build_public_presentation_name_rows(
    candidates: list[dict[str, Any]],
    registry_rows: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Build display hints while keeping registry values authoritative."""
    registry = {
        clean(row.get("fideID")): row
        for row in registry_rows
        if clean(row.get("fideID"))
    }
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for candidate in candidates:
        fide_id = clean(candidate.get("fideID"))
        chinese_name = sanitize_person_name(candidate.get("suggestedChineseName"))
        tier = clean(candidate.get("queueTier"))
        if not fide_id or fide_id not in registry or not chinese_name:
            continue
        if clean(registry[fide_id].get("chineseName")):
            continue
        if chinese_name in FORBIDDEN_PRESENTATION_NAMES.get(fide_id, set()):
            raise ValueError(f"historical wrong presentation name resurfaced: {fide_id}={chinese_name}")
        if tier == "suggested-high" and candidate.get("presentationEligible"):
            confidence, policy = "high", "default"
        elif tier == "suggested-medium" and not candidate.get("presentationEligible"):
            confidence, policy = "medium", "detail-only"
        else:
            # Conflicts and inconsistent eligibility remain in the private
            # maintainer queue and never enter a public projection.
            continue
        if fide_id in seen:
            raise ValueError(f"duplicate public presentation-name candidate: {fide_id}")
        seen.add(fide_id)
        result.append({
            "fideID": fide_id,
            "suggestedChineseName": chinese_name,
            "confidence": confidence,
            "displayPolicy": policy,
            "identityBasis": f"presentation-{confidence}-name",
        })
    order = {"high": 0, "medium": 1}
    result.sort(key=lambda row: (order[row["confidence"]], row["fideID"]))
    return result


def build_fide_candidates(
    players: list[DomesticPlayer],
    registry_path: pathlib.Path,
    player_events_path: pathlib.Path = PLAYER_EVENTS_CSV,
    chinese_name_candidates: list[dict[str, Any]] | None = None,
    promotion_review_path: pathlib.Path = PROMOTION_REVIEW,
    public_event_details_root: pathlib.Path = PUBLIC_EVENT_DETAILS,
) -> list[dict[str, Any]]:
    if not registry_path.exists():
        return []
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    name_hits: dict[str, dict[str, Any]] = {}
    ambiguous: set[str] = set()
    for fide_player in registry:
        fide_id = clean(fide_player.get("fideID"))
        if not fide_id:
            continue
        values = [fide_player.get("chineseName"), fide_player.get("displayName"), *(fide_player.get("aliases") or [])]
        for value in values:
            key = normalized_name(value)
            if not key:
                continue
            existing = name_hits.get(key)
            if existing and existing["fideID"] != fide_id:
                ambiguous.add(key)
            else:
                name_hits[key] = {
                    "fideID": fide_id,
                    "displayName": clean(fide_player.get("displayName") or fide_player.get("name")),
                    "standard": int(fide_player.get("standard") or 0),
                    "title": clean(fide_player.get("title")),
                    "sex": clean(fide_player.get("sex")),
                    "birthYear": parse_int(fide_player.get("birthYear")),
                }
    for key in ambiguous:
        name_hits.pop(key, None)

    # Public event details have already resolved roster rows to registry FIDE
    # IDs.  Reusing that explicit same-event projection closes cases such as
    # Jin Hongtao at tnr990921, where the raw Chinese-language row omitted the
    # ID but the bilingual/public roster identifies the FIDE card.
    event_detail_fides: dict[tuple[str, str], set[str]] = {}
    if public_event_details_root.is_dir():
        for path in public_event_details_root.glob("tnr*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            event_id = clean(payload.get("tournamentID")) or path.stem.removeprefix("tnr")
            for row in [*(payload.get("players") or []), *(payload.get("standings") or [])]:
                fide_id = clean(row.get("fideID"))
                name_key = normalized_name(row.get("chineseName") or row.get("displayName") or row.get("name"))
                if fide_id and name_key:
                    event_detail_fides.setdefault((event_id, name_key), set()).add(fide_id)

    player_events = read_player_event_evidence(player_events_path)
    event_ids_by_fide: dict[str, set[str]] = {}
    event_names_by_fide: dict[str, dict[str, set[str]]] = {}
    event_clubs_by_fide: dict[str, set[str]] = {}
    event_name_fides: dict[str, set[str]] = {}
    for row in player_events:
        fide_id = row["fide_id"]
        name_key = normalized_name(row.get("player_name"))
        event_ids_by_fide.setdefault(fide_id, set()).add(row["tnrid"])
        if name_key:
            event_names_by_fide.setdefault(fide_id, {}).setdefault(name_key, set()).add(row["tnrid"])
            event_name_fides.setdefault(name_key, set()).add(fide_id)
        club = distinctive_club(row.get("club"))
        if club:
            event_clubs_by_fide.setdefault(fide_id, set()).add(club)

    suggested_names = {
        row["fideID"]: row.get("suggestedChineseName", "")
        for row in chinese_name_candidates or []
        if row.get("presentationEligible") and row.get("suggestedChineseName")
    }
    promotion_qualified: set[str] = set()
    if promotion_review_path.exists():
        try:
            promotion_qualified = {
                clean(row.get("playerID"))
                for row in json.loads(promotion_review_path.read_text(encoding="utf-8"))
                if row.get("promotionQualified") and clean(row.get("playerID"))
            }
        except (OSError, json.JSONDecodeError):
            promotion_qualified = set()

    domestic_name_counts = Counter(identity_name(player) for player in players if identity_name(player))
    result: list[dict[str, Any]] = []
    for player in players:
        if player.fide_id:
            continue
        name_key = identity_name(player)
        player_keys = identity_keys(player)
        matched_hits = {
            hit["fideID"]: hit
            for key in player_keys
            if (hit := name_hits.get(key))
        }
        # Different aliases resolving to different FIDE cards is a hard
        # ambiguity.  A large same-name domestic cluster is not: every member
        # still receives its own reviewable candidate.
        if len(matched_hits) != 1:
            continue
        hit = next(iter(matched_hits.values()))
        cluster_size = domestic_name_counts.get(name_key, 1)
        player_sexes = {clean(player.sex), *(clean(s.sex) for s in player.sightings)}
        player_sexes.discard("")
        if hit["sex"] and player_sexes and player_sexes != {hit["sex"]}:
            continue
        player_birth_years = {
            year
            for year in (
                player.birth_year,
                *(s.birth_year for s in player.sightings),
            )
            if year is not None
        }
        if hit["birthYear"] is not None and player_birth_years and player_birth_years != {hit["birthYear"]}:
            continue
        weights = {"uniqueExactFideName": 25}
        score = 25
        pinyin_or_alias_fallback = name_hits.get(name_key, {}).get("fideID") != hit["fideID"]
        matched_name_keys = sorted(
            key for key in player_keys
            if name_hits.get(key, {}).get("fideID") == hit["fideID"]
        )
        evidence: list[str] = [
            "注册表唯一精确拼音或别名候选"
            if pinyin_or_alias_fallback
            else "注册表唯一精确同名"
        ]
        event_count = len({s.event_id for s in player.sightings if s.event_id})
        if event_count > 1:
            weights["crossEvent"] = 30
            score += 30
        if cluster_size == 1:
            weights["uniqueDomesticName"] = 25
            score += 25
        fide_id = hit["fideID"]
        domestic_events = {
            clean(s.event_id).removeprefix("chess-results-tnr").removeprefix("tnr")
            for s in player.sightings if clean(s.event_id)
        }
        direct_events = set(domestic_events & event_ids_by_fide.get(fide_id, set()))
        direct_events.update(
            event_id for event_id in domestic_events
            if any(
                event_detail_fides.get((event_id, key)) == {fide_id}
                for key in player_keys
            )
        )
        direct_events = sorted(direct_events)
        if direct_events:
            weights["directSameEventFide"] = 50
            score += 50
            evidence.append(f"同一赛事直接关联 FIDE（{len(direct_events)} 项）")
        # Source-name repetition is independent evidence only for the primary
        # domestic identity name. A Latin/pinyin fallback merely repeats the
        # registry alias and must not unlock an automatic presentation group.
        source_name_keys = (
            [name_key]
            if event_names_by_fide.get(fide_id, {}).get(name_key)
            else []
        )
        source_name_events = {
            event_id
            for key in source_name_keys
            for event_id in event_names_by_fide.get(fide_id, {}).get(key, set())
        }
        unambiguous_source_name = bool(
            source_name_events
            and all(event_name_fides.get(key) == {fide_id} for key in source_name_keys)
        )
        if unambiguous_source_name:
            value = 45 if len(source_name_events) >= 2 else 35
            weights["sourceFideNameEvidence"] = value
            score += value
            evidence.append(f"FIDE 搜索结果中同名出现 {len(source_name_events)} 项赛事")
        own_clubs = {distinctive_club(s.club) for s in player.sightings if distinctive_club(s.club)}
        club_matches = sorted(own_clubs & event_clubs_by_fide.get(fide_id, set()))
        if club_matches:
            weights["distinctiveClubMatch"] = 40
            score += 40
            evidence.append("特色参赛单位一致")
        if player.domestic_id in promotion_qualified:
            weights["qualifiedProgression"] = 20
            score += 20
            evidence.append("达到晋级线并存在升级轨迹")
        if (
            pinyin_or_alias_fallback
            and cluster_size == 1
            and event_count <= 1
            and not direct_events
            and not unambiguous_source_name
            and not club_matches
        ):
            # One isolated observation plus transliteration is too broad even
            # for the maintainer queue. Repeated domestic sightings or an
            # independent event/club edge is required.
            continue
        score = max(0, min(100, score))
        presentation_eligible = bool(
            not pinyin_or_alias_fallback
            # Repeated/global name evidence orders the review queue but is not
            # member-level proof. Automatic presentation requires this exact
            # domestic observation to share an event FIDE ID or a distinctive
            # club with the registry candidate.
            and (direct_events or club_matches)
        )
        result.append({
            "domesticID": player.domestic_id,
            "domesticName": player.display_name,
            "candidateFideID": hit["fideID"],
            "candidateFideName": hit["displayName"],
            "candidateStandard": hit["standard"],
            "candidateTitle": hit["title"],
            "matchBasis": "；".join(evidence),
            "matchedNameKeys": matched_name_keys,
            "sameNameClusterSize": cluster_size,
            "score": score,
            "queueTier": "suggested-high" if presentation_eligible or score >= 70 else "suggested-medium" if score >= 45 else "low",
            "weights": weights,
            "presentationEligible": presentation_eligible,
            "directSameEventIDs": direct_events,
            "matchedClubs": club_matches,
            "sourceNameEventCount": len(source_name_events),
            "suggestedChineseName": suggested_names.get(fide_id, ""),
            "sightingCount": len(player.sightings),
            "eventIDs": sorted({s.event_id for s in player.sightings if s.event_id}),
            "clubs": sorted({s.club for s in player.sightings if s.club}),
            "reviewRequired": True,
            "warning": "仅为候选，不得自动写入 player-identity-links.csv",
        })
    result.sort(key=lambda row: (-row["score"], row["domesticName"], row["domesticID"]))
    return result


def provisional_domestic_id(sighting: Sighting) -> str:
    return "domestic-" + hashlib.sha256(sighting.sighting_id.encode("utf-8")).hexdigest()[:12]


def generated_sighting_id(row: dict[str, Any]) -> str:
    basis = "|".join(
        [
            clean(row.get("source")),
            clean(row.get("event_id")),
            clean(row.get("group")),
            clean(row.get("source_player_no")),
            clean(row.get("player_name")),
            clean(row.get("chinese_name")),
            clean(row.get("pinyin_name")),
        ]
    )
    return "sighting-" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def write_output(
    players: list[DomesticPlayer],
    sightings: list[Sighting],
    links: list[IdentityLink],
    name_groups: list[dict[str, Any]],
    identity_candidates: list[dict[str, Any]],
    fide_candidates: list[dict[str, Any]],
    chinese_name_candidates: list[dict[str, Any]],
    output_root: pathlib.Path,
    dry_run: bool,
    conflict_edges: list[dict[str, Any]] | None = None,
    presentation_groups: list[dict[str, Any]] | None = None,
    fide_registry_path: pathlib.Path = FIDE_REGISTRY,
) -> None:
    import sys
    if str(REPO_ROOT / "Scripts") not in sys.path:
        sys.path.append(str(REPO_ROOT / "Scripts"))
    try:
        from snapshot_context import snapshot_id
        sid = snapshot_id()
    except Exception:
        sid = "unknown"

    generated_at = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    confidence_totals = identity_confidence_totals(players)
    manifest = {
        "schemaVersion": 2,
        "snapshotId": sid,
        "generatedAt": generated_at,
        "identityRuleVersion": IDENTITY_CLUSTER_RULE_VERSION,
        # Public manifest lists publicly served resources ONLY (review §3.3);
        # builder inputs and maintainer paths live in the private build
        # summary (data/generated/audit/identity-workbench-summary.json).
        "storage": {
            "players": "data/registry/domestic/players.json",
            "detailShards": "data/registry/domestic/shards/{prefix}.json",
            "identityLinks": "data/registry/domestic/identity-links.json",
            "presentationGroups": "data/registry/domestic/presentation-groups.json",
            "presentationNames": "data/identity/presentation-names.json",
            "identityQuality": "data/registry/domestic/identity-quality.json",
        },
        "totals": {
            "sightings": len(sightings),
            "domesticPlayers": len(players),
            "linkedToFIDE": sum(1 for player in players if player.fide_id),
            "unlinked": sum(1 for player in players if not player.fide_id),
            "identityLinks": len(links),
            **confidence_totals,
            "sameNameConflicts": sum(1 for player in players if player.confidence.get("sameNameConflictCount", 0)),
            "uniqueNameCount": len({identity_name(player) for player in players if identity_name(player)}),
            "sameNameGroups": len(name_groups),
            "fideLinkCandidates": len(fide_candidates),
            "highPriorityFideLinkCandidates": sum(candidate.get("queueTier") == "suggested-high" for candidate in fide_candidates),
            "chineseNameCandidates": len(chinese_name_candidates),
            "highPriorityChineseNameCandidates": sum(candidate.get("queueTier") == "suggested-high" for candidate in chinese_name_candidates),
            "conflictingChineseNameCandidates": sum(candidate.get("queueTier") == "conflict" for candidate in chinese_name_candidates),
            "identityCandidates": len(identity_candidates),
            "highPriorityIdentityCandidates": sum(candidate.get("queueTier") == "suggested-high" for candidate in identity_candidates),
            "parentOnlyNameGroups": sum(group.get("adjudicationMode") == "parent-only" for group in name_groups),
        },
    }
    if dry_run:
        return
    output_root.mkdir(parents=True, exist_ok=True)
    write_json(output_root / "manifest.json", manifest)
    # The full per-entity payload (with sightings) exceeds the 25 MiB
    # per-file hosting cap since event observations landed. The public
    # monolith carries summaries only; detail views read the hash shards,
    # and derived builders read the machine-layer full projection.
    full_payloads = [player.payload() for player in players]
    generated_root = REPO_ROOT / "data" / "generated"
    generated_root.mkdir(parents=True, exist_ok=True)
    write_json(generated_root / "domestic-players-full.json", full_payloads)
    summary_payloads = [
        {key: value for key, value in payload.items() if key not in ("sightings", "confidence")}
        for payload in full_payloads
    ]
    write_json(output_root / "players.json", summary_payloads)

    # 产品级门禁：如果 high candidates > 0 而 presentation groups = 0，构建抛出告警并打印原因
    high_candidates = sum(c.get("queueTier") == "suggested-high" for c in identity_candidates)
    if high_candidates > 0 and len(presentation_groups or []) == 0:
        print(f"WARNING: high priority identity candidates count is {high_candidates}, but presentation groups count is 0.")
        print("Analysis of high priority candidates:")
        for c in identity_candidates:
            if c.get("queueTier") == "suggested-high" and not c.get("presentationEligible"):
                print(f"  Candidate {c.get('candidateID')} ({c.get('displayName')}): eligibility details -> {c.get('evidenceSummary')}")

    # Display-only high-confidence aggregation (review §4): a small public
    # projection the frontend uses to merge cards; disputes split it on the
    # next projection without touching any Person/observation fact.
    write_json(output_root / "presentation-groups.json", {
        "schemaVersion": 2,
        "snapshotId": sid,
        "generatedAt": generated_at,
        "algorithmVersion": IDENTITY_CLUSTER_RULE_VERSION,
        "identityBasis": "presentation-high",
        "note": "成员级证据通过硬冲突否决后的高置信展示聚合；不覆盖 registry，质疑可强制拆分",
        "totals": {
            "groups": len(presentation_groups or []),
            "entities": sum(len(g["members"]) + (1 if g.get("canonicalFideID") else 0) for g in (presentation_groups or [])),
        },
        "groups": presentation_groups or [],
    })
    # Presentation hints never mutate the registry. High confidence is a
    # default frontend fallback; medium confidence is detail-only; conflicts
    # remain private review material.
    PRESENTATION_NAMES.parent.mkdir(parents=True, exist_ok=True)
    registry_rows = json.loads(fide_registry_path.read_text(encoding="utf-8")) if fide_registry_path.exists() else []
    public_name_rows = build_public_presentation_name_rows(chinese_name_candidates, registry_rows)
    write_json(PRESENTATION_NAMES, {
        "schemaVersion": 2,
        "snapshotId": sid,
        "generatedAt": generated_at,
        "note": "展示候选不覆盖 registry；高置信默认展示，中置信仅详情提示，冲突不公开",
        "totals": {
            "players": len(public_name_rows),
            "high": sum(row["confidence"] == "high" for row in public_name_rows),
            "medium": sum(row["confidence"] == "medium" for row in public_name_rows),
        },
        "players": public_name_rows,
    })
    write_domestic_search_and_shards(output_root, players)
    # Flat sightings + search-index are build intermediates, not product
    # surfaces; they left the deployed tree to stay under the hosting size cap.
    write_json(generated_root / "domestic-sightings.json", [sighting.payload() for sighting in sightings])
    write_json(output_root / "identity-links.json", [link.payload() for link in links])
    # Raw adjudication material (clubs, candidate edges, minors' event
    # trails) belongs to the repo-external maintainer workbench, not the
    # public repository (review §4.6). The repo keeps aggregate counts only.
    try:
        from source_policy import local_state_root
        workbench = local_state_root() / "identity-workbench"
    except Exception:  # pragma: no cover
        workbench = REPO_ROOT / ".identity-workbench"
    workbench.mkdir(parents=True, exist_ok=True)
    write_json(workbench / "identity-name-groups.json", name_groups)
    write_json(workbench / "identity-candidates.json", identity_candidates)
    # Negative knowledge: hard-conflict pairs are banned edges; a rejected or
    # impossible merge suggestion must never resurface (tombstones).
    write_json(workbench / "identity-conflict-edges.json", conflict_edges or [])
    write_json(workbench / "fide-link-candidates.json", fide_candidates)
    write_json(workbench / "chinese-name-candidates.json", chinese_name_candidates)
    review_queue = build_unified_review_queue(
        identity_candidates, fide_candidates, chinese_name_candidates,
    )
    write_json(workbench / "review-queue.json", review_queue)
    review_rows = [player.payload() for player in players if player.confidence.get("reviewRequired")]
    review_rows.sort(key=lambda player: (player["confidence"]["score"], -player["confidence"]["sameNameConflictCount"], player.get("displayName", "")))
    write_json(workbench / "identity-review.json", review_rows)
    audit_root = REPO_ROOT / "data" / "generated" / "audit"
    audit_root.mkdir(parents=True, exist_ok=True)
    write_json(audit_root / "identity-workbench-summary.json", {
        "schemaVersion": 1,
        "generatedAt": generated_at,
        "workbench": "local_state_root()/identity-workbench (repo-external)",
        "totals": {
            "identityCandidates": len(identity_candidates),
            "suggestedHigh": sum(c.get("queueTier") == "suggested-high" for c in identity_candidates),
            "conflictEdges": len(conflict_edges or []),
            "fideLinkCandidates": len(fide_candidates),
            "highPriorityFideLinkCandidates": sum(c.get("queueTier") == "suggested-high" for c in fide_candidates),
            "chineseNameCandidates": len(chinese_name_candidates),
            "highPriorityChineseNameCandidates": sum(c.get("queueTier") == "suggested-high" for c in chinese_name_candidates),
            "conflictingChineseNameCandidates": sum(c.get("queueTier") == "conflict" for c in chinese_name_candidates),
            "unifiedReviewQueue": len(review_queue),
            "reviewRows": len(review_rows),
        },
    })
    for stale_root, names in (
        (output_root, ("identity-name-groups.json", "identity-candidates.json",
                       "fide-link-candidates.json", "chinese-name-candidates.json",
                       "review-queue.json", "identity-review.json")),
        (audit_root, ("identity-name-groups.json", "identity-candidates.json",
                      "identity-conflict-edges.json", "fide-link-candidates.json",
                      "chinese-name-candidates.json", "review-queue.json",
                      "identity-review.json")),
    ):
        for stale in names:
            stale_path = stale_root / stale
            if stale_path.exists():
                stale_path.unlink()


def build_unified_review_queue(
    identity_candidates: list[dict[str, Any]],
    fide_candidates: list[dict[str, Any]],
    chinese_name_candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """One maintainer queue ordered by likely user value and decision risk."""
    rows: list[dict[str, Any]] = []
    for candidate in chinese_name_candidates:
        priority = 30 if candidate.get("queueTier") == "conflict" else 40 if candidate.get("queueTier") == "suggested-high" else 70
        rows.append({
            "reviewType": "chinese-name",
            "priority": priority,
            "candidateID": candidate.get("candidateID"),
            "subjectID": candidate.get("fideID"),
            "display": candidate.get("suggestedChineseName") or " / ".join(candidate.get("candidateNames") or []),
            "queueTier": candidate.get("queueTier"),
            "eventCount": candidate.get("eventCount", 0),
        })
    for candidate in fide_candidates:
        if candidate.get("directSameEventIDs"):
            priority = 20
        elif candidate.get("matchedClubs"):
            priority = 25
        elif candidate.get("presentationEligible"):
            priority = 35
        elif candidate.get("queueTier") == "suggested-high":
            priority = 45
        else:
            priority = 80
        rows.append({
            "reviewType": "domestic-fide-link",
            "priority": priority,
            "candidateID": f"fide-link-{candidate.get('domesticID')}-{candidate.get('candidateFideID')}",
            "subjectID": candidate.get("domesticID"),
            "targetID": candidate.get("candidateFideID"),
            "display": candidate.get("domesticName"),
            "queueTier": candidate.get("queueTier"),
            "score": candidate.get("score", 0),
            "valueScore": candidate.get("candidateStandard", 0),
            "eventCount": candidate.get("sightingCount", 0),
        })
    for candidate in identity_candidates:
        priority = 10 if candidate.get("queueTier") == "suggested-high" and not candidate.get("presentationEligible") else 15 if candidate.get("queueTier") == "suggested-high" else 90
        rows.append({
            "reviewType": "domestic-domestic-link",
            "priority": priority,
            "candidateID": candidate.get("candidateID"),
            "subjectID": ",".join(candidate.get("domesticIDs") or []),
            "display": candidate.get("displayName"),
            "queueTier": candidate.get("queueTier"),
            "score": candidate.get("score", 0),
            "eventCount": len(candidate.get("eventIDs") or []),
        })
    rows.sort(key=lambda row: (row["priority"], -int(row.get("valueScore") or 0), -int(row.get("eventCount") or 0), -int(row.get("score") or 0), str(row.get("candidateID") or "")))
    return rows


def write_domestic_search_and_shards(output_root: pathlib.Path, players: list[DomesticPlayer]) -> None:
    shards: dict[str, list[dict[str, Any]]] = {}
    search_rows: list[dict[str, Any]] = []
    for player in players:
        payload = player.payload()
        prefix = hashlib.sha256(player.domestic_id.encode("utf-8")).hexdigest()[:2]
        detail_path = f"data/registry/domestic/shards/{prefix}.json"
        shards.setdefault(prefix, []).append(payload)
        search_rows.append(without_empty({
            key: payload.get(key)
            for key in (
                "id", "domesticID", "displayName", "chineseName", "pinyin",
                "federation", "sex", "birthYear", "entityType", "aliases",
                "domesticEligibilityBasis", "publicIdentityStatus", "sightingCount",
            )
        } | {
            "detailPath": detail_path,
            "eventYears": sorted({s.event_date[:4] for s in player.sightings if len(s.event_date) >= 4}),
            "eventNames": ordered_unique([s.event_name for s in player.sightings])[:3],
            "publicLocation": public_location(player),
        }))
    write_json(REPO_ROOT / "data" / "generated" / "domestic-search-index.json", search_rows)
    shard_root = output_root / "shards"
    shard_root.mkdir(parents=True, exist_ok=True)
    for prefix, rows in shards.items():
        write_json(shard_root / f"{prefix}.json", rows)


def clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def parse_int(value: Any) -> int | None:
    text = clean(value)
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def public_location(player: DomesticPlayer) -> str:
    if player.province:
        return player.province
    return location_from_text(" ".join(s.club for s in player.sightings if s.club))


def location_from_text(text: str) -> str:
    """Reduce a club/school string to a province/city-level public location."""
    if not text:
        return ""
    for name in ("北京", "上海", "天津", "重庆", "河北", "山西", "辽宁", "吉林", "黑龙江", "江苏", "浙江", "安徽", "福建", "江西", "山东", "河南", "湖北", "湖南", "广东", "海南", "四川", "贵州", "云南", "陕西", "甘肃", "青海", "内蒙古", "广西", "西藏", "宁夏", "新疆", "香港", "澳门"):
        if name in text:
            return name
    match = re.search(r"([\u4e00-\u9fff]{2,4}市)", text)
    return match.group(1) if match else ""


def ordered_unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = clean(value)
        key = cleaned.casefold()
        if cleaned and key not in seen:
            seen.add(key)
            result.append(cleaned)
    return result


def without_empty(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if value not in (None, "", [], {})}


def write_json(path: pathlib.Path, data: Any) -> None:
    write_stable_json(path, data, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    raise SystemExit(main())
