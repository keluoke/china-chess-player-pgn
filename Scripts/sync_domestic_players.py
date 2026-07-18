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

from stable_json import write_json as write_stable_json


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SIGHTINGS_CSV = REPO_ROOT / "data" / "manual" / "domestic-player-sightings.csv"
OBSERVATIONS_CSV = REPO_ROOT / "data" / "generated" / "person-observations.csv"
LINKS_CSV = REPO_ROOT / "data" / "manual" / "player-identity-links.csv"
OUTPUT_ROOT = REPO_ROOT / "docs" / "data" / "registry" / "domestic"
FIDE_REGISTRY = REPO_ROOT / "docs" / "data" / "registry" / "players.json"


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
        return without_empty(
            {
                "id": self.canonical_id,
                "domesticID": self.domestic_id,
                "fideID": self.fide_id,
                "displayName": self.display_name,
                "chineseName": self.chinese_name,
                "pinyin": self.pinyin_name,
                "federation": "CHN",
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
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    sightings = read_sightings(args.sightings)
    observation_stats = merge_observations(sightings, read_sightings(args.observations))
    links = read_links(args.links)
    players = build_players(sightings, links)
    assess_identity_confidence(players)
    name_groups = build_identity_name_groups(players)
    identity_candidates, conflict_edges = build_identity_candidates(players)
    presentation_groups = build_presentation_groups(players, identity_candidates, conflict_edges)
    fide_candidates = build_fide_candidates(players, args.fide_registry)
    write_output(players, sightings, links, name_groups, identity_candidates, fide_candidates, args.output_root, args.dry_run, conflict_edges, presentation_groups)

    stats = {
        "sightings": len(sightings),
        "observationEnriched": observation_stats["enriched"],
        "observationAppended": observation_stats["appended"],
        "identityLinks": len(links),
        "domesticPlayers": len(players),
        "linkedToFIDE": sum(1 for player in players if player.fide_id),
        "unlinked": sum(1 for player in players if not player.fide_id),
        "lowConfidence": sum(1 for player in players if player.confidence.get("reviewRequired")),
        "sameNameConflicts": sum(1 for player in players if player.confidence.get("sameNameConflictCount", 0)),
        "uniqueNameCount": len({identity_name(player) for player in players if identity_name(player)}),
        "sameNameGroups": len(name_groups),
        "fideLinkCandidates": len(fide_candidates),
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
            enriched += 1
        else:
            sightings.append(observation)
            appended += 1
    return {"enriched": enriched, "appended": appended}


def read_sightings(path: pathlib.Path) -> list[Sighting]:
    if not path.exists():
        return []
    sightings: list[Sighting] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
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
    player.chinese_name = player.chinese_name or sighting.chinese_name
    player.pinyin_name = player.pinyin_name or sighting.pinyin_name
    player.display_name = player.display_name or sighting.chinese_name or sighting.player_name or sighting.pinyin_name or player.domestic_id
    player.sex = player.sex or sighting.sex
    player.birth_year = player.birth_year or sighting.birth_year
    player.province = player.province or sighting.province
    player.club = player.club or sighting.club
    player.aliases.extend(
        [
            player.domestic_id,
            player.fide_id,
            sighting.player_name,
            sighting.chinese_name,
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


def identity_name(player: DomesticPlayer) -> str:
    name = player.chinese_name or player.display_name or player.pinyin_name
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", clean(name).casefold())


def normalize_club(value: Any) -> str:
    return re.sub(r"(?:有限责任公司|有限公司|体育文化|教育咨询|国际象棋|棋类|俱乐部|学校|协会|中心)", "", clean(value).casefold()).strip()


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
    ("候补", 5), ("棋协大师", 6), ("公开", 6),
)
PROMOTION_SCORE_RATE = 0.65
PROMOTION_WINDOW_DAYS = 730
CANDIDATE_ALGORITHM_VERSION = "identity-candidates-v2"


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
        # Season-level date: mid-year anchor keeps the 24-month promotion
        # window meaningful without fabricating an exact day.
        return dt.date(int(text), 7, 1)
    try:
        return dt.date.fromisoformat(text[:10])
    except (TypeError, ValueError):
        return None


def exact_date(value: str) -> bool:
    return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(value or "").strip()[:10]))


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
            if not low_level or rate is None or rate < PROMOTION_SCORE_RATE or low_date is None:
                continue
            for high_sighting in higher.sightings:
                high_level = group_level(high_sighting)
                high_date = parse_date(high_sighting.event_date)
                if high_level != low_level + 1 or high_date is None:
                    continue
                gap = (high_date - low_date).days
                if 0 <= gap <= PROMOTION_WINDOW_DAYS:
                    candidate = {
                        "lowerGroup": low_sighting.group,
                        "scoreRate": round(rate, 3),
                        "higherGroup": high_sighting.group,
                        "monthsBetween": round(gap / 30),
                        "exactDates": exact_date(low_sighting.event_date) and exact_date(high_sighting.event_date),
                    }
                    if best is None or (candidate["exactDates"] and not best["exactDates"]):
                        best = candidate
                    if best["exactDates"]:
                        return best
    return best


def sexes_consistent(left: DomesticPlayer, right: DomesticPlayer) -> bool:
    """Known and equal on both sides (presentation-grouping requirement)."""
    left_sex = {s.sex for s in left.sightings if s.sex} or ({left.sex} if left.sex else set())
    right_sex = {s.sex for s in right.sightings if s.sex} or ({right.sex} if right.sex else set())
    return bool(left_sex) and bool(right_sex) and left_sex == right_sex and len(left_sex) == 1


def hard_conflicts(left: DomesticPlayer, right: DomesticPlayer) -> list[str]:
    conflicts: list[str] = []
    left_events = {s.event_id for s in left.sightings if s.event_id}
    right_events = {s.event_id for s in right.sightings if s.event_id}
    shared = left_events & right_events
    if shared:
        # Same person cannot hold two roster slots in one section capture.
        conflicts.append(f"concurrent-event:{sorted(shared)[0]}")
    left_sex = {s.sex for s in left.sightings if s.sex}
    right_sex = {s.sex for s in right.sightings if s.sex}
    if left_sex and right_sex and left_sex.isdisjoint(right_sex):
        conflicts.append("sex-conflict")
    left_birth = {s.birth_year for s in left.sightings if s.birth_year}
    right_birth = {s.birth_year for s in right.sightings if s.birth_year}
    if left_birth and right_birth and left_birth.isdisjoint(right_birth):
        conflicts.append("birth-year-conflict")
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
    if left_clubs & right_clubs:
        weights["clubConsistency"] = 25
        evidence_kinds += 1
        summary.append("俱乐部一致")

    promotion = promotion_evidence(left, right)
    if promotion:
        weights["promotionPattern"] = 35
        evidence_kinds += 1
        summary.append(
            f"{promotion['lowerGroup']} 得分率 {promotion['scoreRate']:.0%} → "
            f"{promotion['monthsBetween']} 个月后出现在 {promotion['higherGroup']}"
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
    # Review §4.2: "同俱乐部 + 真实晋级轨迹" (same club + genuine promotion
    # pattern, sexes consistent, no hard conflicts) is high-confidence BY
    # ITSELF. Display aggregation additionally requires the promotion order
    # to rest on exact event dates.
    club_plus_promotion = bool(weights.get("clubConsistency")) and bool(promotion) and sexes_consistent(left, right)
    if club_plus_promotion or (score >= 70 and evidence_kinds >= 2):
        tier = "suggested-high"
    elif score >= 45:
        tier = "suggested-medium"
    else:
        tier = "low"
    presentation_eligible = bool(club_plus_promotion and promotion and promotion.get("exactDates"))
    return {
        "presentationEligible": presentation_eligible,
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
) -> list[dict[str, Any]]:
    """High-confidence display-only aggregation (review §4.1–4.5).

    - Edges: presentationEligible candidate pairs (same club + exact-date
      promotion pattern + consistent sex + no hard conflicts) minus disputed/
      tombstoned pairs.
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

    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        parent[find(a)] = find(b)

    eligible_edges: list[tuple[str, str]] = []
    for candidate in identity_candidates:
        if not candidate.get("presentationEligible"):
            continue
        ids = candidate.get("domesticIDs") or []
        if len(ids) != 2:
            continue
        hash_value = pair_hash(ids[0], ids[1])
        if hash_value in disputes or hash_value in conflict_pairs:
            continue
        eligible_edges.append((ids[0], ids[1]))
        union(ids[0], ids[1])

    components: dict[str, set[str]] = {}
    for a, b in eligible_edges:
        components.setdefault(find(a), set()).update([a, b])

    groups: list[dict[str, Any]] = []
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
            "disputeEntry": True,
        })
    groups.sort(key=lambda group: group["groupID"])
    return groups


def build_fide_candidates(players: list[DomesticPlayer], registry_path: pathlib.Path) -> list[dict[str, Any]]:
    if not registry_path.exists():
        return []
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    name_hits: dict[str, dict[str, str]] = {}
    ambiguous: set[str] = set()
    for fide_player in registry:
        fide_id = clean(fide_player.get("fideID"))
        if not fide_id:
            continue
        values = [fide_player.get("chineseName"), fide_player.get("displayName"), *(fide_player.get("aliases") or [])]
        for value in values:
            key = re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", clean(value).casefold())
            if not key:
                continue
            existing = name_hits.get(key)
            if existing and existing["fideID"] != fide_id:
                ambiguous.add(key)
            else:
                name_hits[key] = {"fideID": fide_id, "displayName": clean(fide_player.get("displayName") or fide_player.get("name"))}
    for key in ambiguous:
        name_hits.pop(key, None)

    domestic_name_counts = Counter(identity_name(player) for player in players if identity_name(player))
    result: list[dict[str, Any]] = []
    for player in players:
        name_key = identity_name(player)
        hit = name_hits.get(name_key)
        if not hit:
            continue
        cluster_size = domestic_name_counts.get(name_key, 1)
        if cluster_size >= 3:
            continue
        weights = {"uniqueExactFideName": 25}
        score = 25
        if len({s.event_id for s in player.sightings if s.event_id}) > 1:
            weights["crossEvent"] = 30
            score += 30
        if cluster_size == 1:
            weights["uniqueDomesticName"] = 25
            score += 25
        result.append({
            "domesticID": player.domestic_id,
            "domesticName": player.display_name,
            "candidateFideID": hit["fideID"],
            "candidateFideName": hit["displayName"],
            "matchBasis": "注册表唯一精确同名",
            "score": score,
            "queueTier": "high" if score >= 70 else "medium" if score >= 45 else "low",
            "weights": weights,
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
    output_root: pathlib.Path,
    dry_run: bool,
    conflict_edges: list[dict[str, Any]] | None = None,
    presentation_groups: list[dict[str, Any]] | None = None,
) -> None:
    generated_at = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    manifest = {
        "schemaVersion": 1,
        "generatedAt": generated_at,
        # Public manifest lists publicly served resources ONLY (review §3.3);
        # builder inputs and maintainer paths live in the private build
        # summary (data/generated/audit/identity-workbench-summary.json).
        "storage": {
            "players": "data/registry/domestic/players.json",
            "detailShards": "data/registry/domestic/shards/{prefix}.json",
            "identityLinks": "data/registry/domestic/identity-links.json",
        },
        "totals": {
            "sightings": len(sightings),
            "domesticPlayers": len(players),
            "linkedToFIDE": sum(1 for player in players if player.fide_id),
            "unlinked": sum(1 for player in players if not player.fide_id),
            "identityLinks": len(links),
            "lowConfidence": sum(1 for player in players if player.confidence.get("reviewRequired")),
            "sameNameConflicts": sum(1 for player in players if player.confidence.get("sameNameConflictCount", 0)),
            "uniqueNameCount": len({identity_name(player) for player in players if identity_name(player)}),
            "sameNameGroups": len(name_groups),
            "fideLinkCandidates": len(fide_candidates),
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
    # Display-only high-confidence aggregation (review §4): a small public
    # projection the frontend uses to merge cards; disputes split it on the
    # next projection without touching any Person/observation fact.
    write_json(output_root / "presentation-groups.json", {
        "schemaVersion": 1,
        "generatedAt": generated_at,
        "identityBasis": "presentation-high",
        "note": "机器高置信展示聚合；确认合并请走 player-identity-links.csv，质疑请提交 presentation-disputes",
        "totals": {
            "groups": len(presentation_groups or []),
            "entities": sum(len(g["members"]) for g in (presentation_groups or [])),
        },
        "groups": presentation_groups or [],
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
            "reviewRows": len(review_rows),
        },
    })
    for stale_root, names in (
        (output_root, ("identity-name-groups.json", "identity-candidates.json",
                       "fide-link-candidates.json", "identity-review.json")),
        (audit_root, ("identity-name-groups.json", "identity-candidates.json",
                      "identity-conflict-edges.json", "fide-link-candidates.json",
                      "identity-review.json")),
    ):
        for stale in names:
            stale_path = stale_root / stale
            if stale_path.exists():
                stale_path.unlink()


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
                "publicIdentityStatus", "sightingCount",
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
