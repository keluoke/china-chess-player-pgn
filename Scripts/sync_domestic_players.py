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

    def payload(self) -> dict[str, Any]:
        return without_empty(
            {
                "sightingID": self.sighting_id,
                "source": self.source,
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
                "sourcePlayerNo": self.source_player_no,
                "sourceURL": self.source_url,
                "notes": self.notes,
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
                "evidence": self.evidence,
                "sourceURL": self.source_url,
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
    parser.add_argument("--links", type=pathlib.Path, default=LINKS_CSV)
    parser.add_argument("--output-root", type=pathlib.Path, default=OUTPUT_ROOT)
    parser.add_argument("--fide-registry", type=pathlib.Path, default=FIDE_REGISTRY)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    sightings = read_sightings(args.sightings)
    links = read_links(args.links)
    players = build_players(sightings, links)
    assess_identity_confidence(players)
    name_groups = build_identity_name_groups(players)
    identity_candidates = build_identity_candidates(players)
    fide_candidates = build_fide_candidates(players, args.fide_registry)
    write_output(players, sightings, links, name_groups, identity_candidates, fide_candidates, args.output_root, args.dry_run)

    stats = {
        "sightings": len(sightings),
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
        "highPriorityIdentityCandidates": sum(candidate.get("queueTier") == "high" for candidate in identity_candidates),
        "parentOnlyNameGroups": sum(group.get("adjudicationMode") == "parent-only" for group in name_groups),
    }
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


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


def build_identity_candidates(players: list[DomesticPlayer]) -> list[dict[str, Any]]:
    grouped: dict[str, list[DomesticPlayer]] = {}
    for player in players:
        key = identity_name(player)
        if key:
            grouped.setdefault(key, []).append(player)
    candidates: list[dict[str, Any]] = []
    for key, members in grouped.items():
        # Three or more provisional entities are deliberately never nominated.
        if len(members) != 2:
            continue
        left, right = members
        weights: dict[str, int] = {"onlyTwoSameNameObservations": 25}
        score = 25
        left_events = {s.event_id for s in left.sightings if s.event_id}
        right_events = {s.event_id for s in right.sightings if s.event_id}
        if left_events and right_events and left_events.isdisjoint(right_events):
            weights["crossEvent"] = 30
            score += 30
        left_clubs = {normalize_club(s.club) for s in left.sightings if normalize_club(s.club)}
        right_clubs = {normalize_club(s.club) for s in right.sightings if normalize_club(s.club)}
        if left_clubs and right_clubs and left_clubs.intersection(right_clubs):
            weights["clubConsistency"] = 20
            score += 20
        left_birth = {s.birth_year for s in left.sightings if s.birth_year}
        right_birth = {s.birth_year for s in right.sightings if s.birth_year}
        if left_birth and right_birth and left_birth != right_birth:
            weights["birthYearConflict"] = -40
            score -= 40
        combined_continuity = age_stage_continuity([*left.sightings, *right.sightings])
        if combined_continuity == "consistent":
            weights["ageContinuity"] = 15
            score += 15
        elif combined_continuity == "conflict":
            weights["ageConflict"] = -30
            score -= 30
        score = max(0, min(100, score))
        candidates.append({
            "candidateID": "identity-candidate-" + hashlib.sha256(f"{left.domestic_id}|{right.domestic_id}".encode("utf-8")).hexdigest()[:16],
            "normalizedName": key,
            "displayName": left.display_name,
            "domesticIDs": [left.domestic_id, right.domestic_id],
            "score": score,
            "queueTier": "high" if score >= 70 else "medium" if score >= 45 else "low",
            "weights": weights,
            "eventIDs": sorted(left_events | right_events),
            "clubs": sorted({s.club for player in members for s in player.sightings if s.club}),
            "ageStageContinuity": combined_continuity,
            "machineNominationAllowed": True,
            "reviewRequired": True,
            "warning": "机器仅提名候选，禁止自动写入 player-identity-links.csv",
        })
    candidates.sort(key=lambda row: (-row["score"], row["displayName"], row["candidateID"]))
    return candidates


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
) -> None:
    generated_at = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    manifest = {
        "schemaVersion": 1,
        "generatedAt": generated_at,
        "storage": {
            "players": "data/registry/domestic/players.json",
            "searchIndex": "data/registry/domestic/search-index.json",
            "detailShards": "data/registry/domestic/shards/{prefix}.json",
            "sightings": "data/registry/domestic/sightings.json",
            "identityLinks": "data/registry/domestic/identity-links.json",
            "identityReview": "data/registry/domestic/identity-review.json",
            "identityNameGroups": "data/registry/domestic/identity-name-groups.json",
            "identityCandidates": "data/registry/domestic/identity-candidates.json",
            "fideLinkCandidates": "data/registry/domestic/fide-link-candidates.json",
            "progressions": "data/registry/domestic/progressions.json",
            "promotionReview": "data/registry/domestic/promotion-review.json",
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
            "highPriorityIdentityCandidates": sum(candidate.get("queueTier") == "high" for candidate in identity_candidates),
            "parentOnlyNameGroups": sum(group.get("adjudicationMode") == "parent-only" for group in name_groups),
        },
    }
    if dry_run:
        return
    output_root.mkdir(parents=True, exist_ok=True)
    write_json(output_root / "manifest.json", manifest)
    write_json(output_root / "players.json", [player.payload() for player in players])
    write_domestic_search_and_shards(output_root, players)
    write_json(output_root / "sightings.json", [sighting.payload() for sighting in sightings])
    write_json(output_root / "identity-links.json", [link.payload() for link in links])
    write_json(output_root / "identity-name-groups.json", name_groups)
    write_json(output_root / "identity-candidates.json", identity_candidates)
    write_json(output_root / "fide-link-candidates.json", fide_candidates)
    review_rows = [player.payload() for player in players if player.confidence.get("reviewRequired")]
    review_rows.sort(key=lambda player: (player["confidence"]["score"], -player["confidence"]["sameNameConflictCount"], player.get("displayName", "")))
    write_json(output_root / "identity-review.json", review_rows)


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
    write_json(output_root / "search-index.json", search_rows)
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
