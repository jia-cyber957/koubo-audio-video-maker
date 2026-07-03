#!/usr/bin/env python3
"""Collect, review, and download stock video material for long-form scripts."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


PEXELS_ENDPOINT = "https://api.pexels.com/v1/videos/search"
PIXABAY_ENDPOINT = "https://pixabay.com/api/videos/"
CACHE_HOURS = 24
API_GAP_SECONDS = 0.8
RAW_RESULTS_PER_PROVIDER = 30
RULE_CANDIDATES_PER_POINT = 4
FINAL_VIDEOS_PER_POINT = 3
ACCEPTED_SCORE = 75
RESERVE_SCORE = 55
RATE_LIMITS = {
    "pexels": {"limit": 180, "window": 3600},
    "pixabay": {"limit": 90, "window": 60},
}
VALID_PRIORITIES = {"high", "medium", "low"}
VALID_PLAN_MODES = {"long_video_chapter_materials", "fine_timed_storyboards"}
ADAPTIVE_SEARCH_STRATEGY = "adaptive_visual_intent"
VISUAL_INTENTS = ("direct", "mixed", "associative", "metaphorical")
MAX_STORYBOARD_SECONDS = 10.0
STOP_WORDS = {
    "a", "an", "and", "at", "by", "for", "from", "in", "into", "of",
    "on", "the", "to", "video", "footage", "stock", "old", "ancient",
    "historical", "history", "traditional", "documentary",
}
CONFLICT_RULES = (
    ({"court", "law"}, {"tennis", "wimbledon", "sport", "athlete", "ball"}, "sports court"),
    ({"ancestral", "hall"}, {"wedding", "bride", "bridal", "bouquet"}, "wedding ceremony"),
    ({"chinese", "china"}, {"hindu", "hinduism", "india", "japanese", "japan"}, "wrong culture"),
    ({"scholar", "confucian"}, {"wizard", "harry", "potter", "quran", "bible"}, "wrong reading context"),
)


class PlanError(ValueError):
    pass


class ReviewError(ValueError):
    pass


@dataclass(frozen=True)
class MaterialItem:
    global_number: int
    chapter_number: int
    chapter_title: str
    chapter_folder: str
    point: dict[str, Any]

    @property
    def point_id(self) -> str:
        return f"{self.global_number:03d}"


@dataclass
class VideoCandidate:
    candidate_id: str
    source: str
    source_id: str
    query: str
    title: str
    tags: list[str]
    video_url: str
    page_url: str
    thumbnail_url: str
    author: str
    width: int
    height: int
    duration: int | float | str | None
    api_rank: int
    rule_score: int = 0
    rule_reasons: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result.pop("rule_score", None)
        result.pop("rule_reasons", None)
        return result

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "VideoCandidate":
        return cls(**value)


class Pipeline:
    def __init__(
        self,
        plan_path: Path,
        output_root: Path,
        start_point: int = 1,
        max_points: int | None = None,
        dry_run: bool = False,
        force: bool = False,
    ) -> None:
        self.plan_path = plan_path
        self.output_root = output_root
        self.start_point = max(1, start_point)
        self.max_points = max_points if max_points is None else max(0, max_points)
        self.dry_run = dry_run
        self.force = force
        self.plan = load_and_validate_plan(plan_path)
        self.title = safe_name(str(self.plan["title"]))
        self.project_dir = output_root / self.title
        self.review_root = self.project_dir / "审核候选"
        self.cache_root = output_root / "_api_cache"
        self.rate_root = self.cache_root / "_rate"
        self.log_path = self.project_dir / "运行日志.txt"
        self.status_path = self.project_dir / "运行状态.json"
        self.items = flatten_material_items(self.plan)
        self.last_api_at = 0.0

    @property
    def adaptive_search(self) -> bool:
        return self.plan.get("search_strategy") == ADAPTIVE_SEARCH_STRATEGY

    def initialize(self) -> None:
        self.project_dir.mkdir(parents=True, exist_ok=True)
        copied_plan = self.project_dir / "素材分段计划.json"
        if self.plan_path.resolve() != copied_plan.resolve():
            shutil.copyfile(self.plan_path, copied_plan)
        self.write_manifest()

    def log(self, message: str) -> None:
        line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}"
        print(line, flush=True)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def selected_items(self) -> list[MaterialItem]:
        selected = [item for item in self.items if item.global_number >= self.start_point]
        if self.max_points is not None:
            selected = selected[: self.max_points]
        return selected

    def round_dir(self, round_number: int) -> Path:
        return self.review_root / f"第{round_number:02d}轮"

    def set_status(
        self,
        state: str,
        stage: str,
        round_number: int | None = None,
        point_id: str = "",
        provider: str = "",
        error: str = "",
    ) -> None:
        write_json(
            self.status_path,
            {
                "state": state,
                "stage": stage,
                "round": round_number,
                "point_id": point_id,
                "provider": provider,
                "error": error,
                "updated_at": utc_now(),
                "action": "Codex must inspect 运行日志.txt and 运行状态.json before resuming."
                if state == "stopped"
                else "",
            },
        )

    def collect(self, round_number: int) -> Path:
        if round_number not in {1, 2, 3, 4, 5, 6}:
            raise SystemExit("Round must be between 1 and 6.")
        self.initialize()
        self.set_status("running", "collect", round_number)
        if round_number > 1:
            self.require_review_complete(round_number - 1)

        states = self.review_states(before_round=round_number)
        seen_ids = self.previous_candidate_ids(round_number)
        items = [item for item in self.selected_items() if self.is_round_eligible(item, round_number, states)]
        needs_dynamic = round_number >= 4 and not (self.adaptive_search and round_number in {4, 5})
        dynamic_queries = self.load_dynamic_queries(round_number, items) if needs_dynamic else {}
        target_dir = self.round_dir(round_number)
        target_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = target_dir / "候选清单.json"
        if manifest_path.exists() and not self.force:
            existing = read_json_required(manifest_path)
            if existing.get("status") == "completed":
                raise RuntimeError(
                    f"Round {round_number} is already complete. Use --force only after checking its reviews."
                )
        if self.force:
            (target_dir / "文字审核结果.json").unlink(missing_ok=True)
            (target_dir / "文字审核任务.json").unlink(missing_ok=True)
            (target_dir / "AI审核结果.json").unlink(missing_ok=True)
            (target_dir / "审核完成.json").unlink(missing_ok=True)
            shutil.rmtree(target_dir / "候选图", ignore_errors=True)

        required_providers = ["Pexels"] if round_number == 1 else ["Pixabay"]
        if round_number in {3, 4, 5, 6}:
            required_providers = ["Pexels", "Pixabay"]
        for provider in required_providers:
            variable = "PEXELS_API_KEY" if provider == "Pexels" else "PIXABAY_API_KEY"
            if not self.dry_run and not user_environment_value(variable):
                raise RuntimeError(f"{variable} is missing; no API requests were made")

        self.log(f"Collect round {round_number}: {len(items)} unresolved material points")

        manifest_points: list[dict[str, Any]] = []
        manifest = {
            "schema_version": 1,
            "title": self.plan["title"],
            "round": round_number,
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "dry_run": self.dry_run,
            "status": "running",
            "error": "",
            "points": manifest_points,
        }
        write_json(manifest_path, manifest)
        for item in items:
            query, providers, visual_intent = self.round_search(item, round_number, dynamic_queries)
            point_errors: list[str] = []
            raw: list[VideoCandidate] = []
            self.log(f"Round {round_number} point {item.point_id}: {query} / {', '.join(providers)}")
            if not self.dry_run:
                for provider in providers:
                    try:
                        found = self.search(provider, query)
                        self.log(f"{provider} returned {len(found)} usable landscape candidates")
                        raw.extend(found)
                    except Exception as exc:  # noqa: BLE001
                        message = f"{provider}: {exc}"
                        point_errors.append(message)
                        self.log(f"ERROR point {item.point_id}: {message}")
                        manifest_points.append(
                            {
                                "point_id": item.point_id,
                                "chapter_number": item.chapter_number,
                                "point_number": int(item.point["number"]),
                                "priority": item.point["priority"],
                                "source_text": item.point["source_text"],
                                "summary": item.point["summary"],
                                "visual_direction": item.point["visual_direction"],
                                "visual_intent": visual_intent,
                                "query": query,
                                "providers": providers,
                                "raw_candidate_count": len(raw),
                                "errors": point_errors,
                                "candidates": [],
                            }
                        )
                        manifest["status"] = "stopped"
                        manifest["error"] = message
                        manifest["updated_at"] = utc_now()
                        write_json(manifest_path, manifest)
                        self.set_status(
                            "stopped", "collect", round_number, item.point_id, provider, message
                        )
                        raise RuntimeError(
                            f"Stopped immediately at round {round_number}, point {item.point_id}, "
                            f"provider {provider}: {exc}"
                        ) from exc

            unique: list[VideoCandidate] = []
            current_ids: set[str] = set()
            for candidate in raw:
                if candidate.candidate_id in seen_ids or candidate.candidate_id in current_ids:
                    continue
                current_ids.add(candidate.candidate_id)
                score_candidate(candidate, query)
                if candidate.rule_reasons and any(reason.startswith("conflict:") for reason in candidate.rule_reasons):
                    continue
                unique.append(candidate)
            unique.sort(key=lambda candidate: (-candidate.rule_score, candidate.api_rank, candidate.candidate_id))
            kept = unique[:RULE_CANDIDATES_PER_POINT]
            seen_ids.update(candidate.candidate_id for candidate in kept)
            manifest_points.append(
                {
                    "point_id": item.point_id,
                    "chapter_number": item.chapter_number,
                    "point_number": int(item.point["number"]),
                    "priority": item.point["priority"],
                    "source_text": item.point["source_text"],
                    "summary": item.point["summary"],
                    "visual_direction": item.point["visual_direction"],
                    "visual_intent": visual_intent,
                    "query": query,
                    "providers": providers,
                    "raw_candidate_count": len(raw),
                    "errors": point_errors,
                    "candidates": [candidate.to_dict() for candidate in kept],
                }
            )
            manifest["updated_at"] = utc_now()
            write_json(manifest_path, manifest)
            self.log(f"Point {item.point_id}: {len(raw)} raw -> {len(kept)} candidates for text review")

        manifest["status"] = "completed"
        manifest["updated_at"] = utc_now()
        write_json(manifest_path, manifest)
        write_json(target_dir / "文字审核任务.json", text_review_task(manifest))
        write_json(target_dir / "文字审核结果.json", text_review_template(manifest))
        self.set_status("completed", "collect", round_number)
        self.log(f"Round {round_number} collection finished: {manifest_path}")
        return manifest_path

    def finalize_review(self, round_number: int) -> Path:
        self.initialize()
        self.set_status("running", "finalize-review", round_number)
        target_dir = self.round_dir(round_number)
        manifest = read_json_required(target_dir / "候选清单.json")
        if manifest.get("status") != "completed":
            raise ReviewError(f"Round {round_number} collection is incomplete; inspect 运行状态.json")
        text_review = read_json_required(target_dir / "文字审核结果.json")
        validate_text_review(manifest, text_review)
        result_path = target_dir / "AI审核结果.json"
        if result_path.exists() and not self.force:
            raise ReviewError(
                f"AI review already exists: {result_path}. Refusing to overwrite; use --force deliberately."
            )
        review_map = review_entries_by_candidate(text_review)
        final_points: list[dict[str, Any]] = []
        for point in manifest["points"]:
            final_points.append(
                {
                    "point_id": point["point_id"],
                    "visual_intent": point.get("visual_intent", "direct"),
                    "contact_sheet": "",
                    "reviews": [final_review_from_text(review_map[candidate["candidate_id"]]) for candidate in point["candidates"]],
                }
            )
        result = {
            "schema_version": 2,
            "round": round_number,
            "instructions": (
                "Final review copies the LLM/Codex text-and-keyword semantic scores from 文字审核结果.json. "
                "Code only validates/transcodes those scores and assigns accepted/reserve/rejected. No visual review, "
                "thumbnail download, or contact sheet inspection is part of this workflow."
            ),
            "points": final_points,
        }
        validate_final_review(manifest, result, text_review)
        write_json(result_path, result)
        self.set_status("completed", "finalize-review", round_number)
        return result_path

    def prepare_queries(self, round_number: int) -> Path:
        if round_number not in {4, 5, 6}:
            raise ReviewError("Dynamic query preparation is only valid for rounds 4, 5, and 6")
        if self.adaptive_search and round_number in {4, 5}:
            raise ReviewError(f"Adaptive round {round_number} uses its planned query; run collect --round {round_number}")
        self.initialize()
        self.require_review_complete(round_number - 1)
        states = self.review_states(before_round=round_number)
        items = [
            item for item in self.selected_items()
            if self.is_round_eligible(item, round_number, states)
        ]
        target_dir = self.round_dir(round_number)
        target_dir.mkdir(parents=True, exist_ok=True)
        result_path = target_dir / "查询结果.json"
        if result_path.exists() and not self.force:
            existing = read_json_required(result_path)
            if any(str(point.get("query", "")).strip() for point in existing.get("points", [])):
                raise ReviewError(f"Query result already contains work: {result_path}; use --force deliberately")

        strategies = {
            4: "Rewrite a more precise stock-video query using the first three rounds' failures.",
            5: "Use a broad, concrete, visibly filmable substitute action; avoid obscure institutions and proper nouns.",
            6: "Use a universal visual metaphor that remains semantically related; avoid random generic footage.",
        }
        if self.adaptive_search:
            strategies[6] = "Rewrite one substantially different direct, mixed, associative, or metaphorical query after reviewing all prior failures."
        task_points = []
        for item in items:
            context = self.previous_review_context(item.point_id, round_number)
            task_points.append(
                {
                    "point_id": item.point_id,
                    "priority": item.point["priority"],
                    "source_text": item.point["source_text"],
                    "summary": item.point["summary"],
                    "visual_direction": item.point["visual_direction"],
                    "previous_queries": context["queries"],
                    "rejected_or_weak_candidates": context["candidates"],
                }
            )
        task = {
            "schema_version": 1,
            "round": round_number,
            "strategy": strategies[round_number],
            "points": task_points,
        }
        result = {
            "schema_version": 1,
            "round": round_number,
            "instructions": (
                "Fill visual_intent, one English ASCII query, and a concise semantic reason for every point."
                if self.adaptive_search else
                "Fill one English ASCII query and a concise semantic reason for every listed point."
            ),
            "points": [
                {
                    "point_id": point["point_id"],
                    **({"visual_intent": ""} if self.adaptive_search else {}),
                    "query": "",
                    "reason": "",
                }
                for point in task_points
            ],
        }
        write_json(target_dir / "查询任务.json", task)
        write_json(result_path, result)
        self.set_status("waiting_for_ai", "query", round_number)
        return result_path

    def review_check(self, round_number: int) -> Path:
        if round_number not in {1, 2, 3, 4, 5, 6}:
            raise ReviewError("Review round must be between 1 and 6")
        self.initialize()
        target_dir = self.round_dir(round_number)
        manifest_path = target_dir / "候选清单.json"
        text_path = target_dir / "文字审核结果.json"
        final_path = target_dir / "AI审核结果.json"
        manifest = read_json_required(manifest_path)
        if manifest.get("status") != "completed":
            raise ReviewError(f"Round {round_number} collection is incomplete")
        text_review = read_json_required(text_path)
        final_review = read_json_required(final_path)
        validate_text_review(manifest, text_review)
        validate_final_review(manifest, final_review, text_review)
        marker = {
            "schema_version": 1,
            "round": round_number,
            "completed_at": utc_now(),
            "manifest_sha256": file_sha256(manifest_path),
            "text_review_sha256": file_sha256(text_path),
            "final_review_sha256": file_sha256(final_path),
        }
        marker_path = target_dir / "审核完成.json"
        write_json(marker_path, marker)
        self.set_status("completed", "review-check", round_number)
        return marker_path

    def workflow_status(self) -> dict[str, Any]:
        self.initialize()
        selection = self.project_dir / "素材选择结果.json"
        if selection.exists() and self.download_selection_complete(selection):
            return {"state": "complete", "next_action": "none", "file": str(selection)}
        for round_number in range(1, 7):
            target_dir = self.round_dir(round_number)
            if round_number >= 4 and not (self.adaptive_search and round_number in {4, 5}):
                query_task = target_dir / "查询任务.json"
                query_result = target_dir / "查询结果.json"
                if not query_task.exists() or not query_result.exists():
                    return status_result(
                        round_number,
                        "prepare_queries",
                        f"Run prepare-queries --round {round_number}",
                    )
                try:
                    self.load_dynamic_queries_for_status(round_number)
                except ReviewError as exc:
                    return status_result(round_number, "fill_queries", str(exc), str(query_result))

            manifest_path = target_dir / "候选清单.json"
            if not manifest_path.exists():
                return status_result(round_number, "collect", f"Run collect --round {round_number}")
            manifest = read_json_required(manifest_path)
            if manifest.get("status") != "completed":
                return status_result(round_number, "resume_collect", "Inspect stop status, then rerun collect")

            text_path = target_dir / "文字审核结果.json"
            try:
                text_review = read_json_required(text_path)
                validate_text_review(manifest, text_review)
            except (ReviewError, FileNotFoundError) as exc:
                return status_result(round_number, "fill_text_review", str(exc), str(text_path))

            final_path = target_dir / "AI审核结果.json"
            if not final_path.exists():
                return status_result(round_number, "finalize_review", f"Run finalize-review --round {round_number}")
            try:
                validate_final_review(manifest, read_json_required(final_path), text_review)
            except ReviewError as exc:
                return status_result(round_number, "fix_final_review", str(exc), str(final_path))

            try:
                self.require_review_complete(round_number)
            except ReviewError as exc:
                return status_result(round_number, "review_check", str(exc))
            if self.adaptive_search:
                states = self.review_states(before_round=round_number + 1)
                if self.adaptive_candidates_complete(states) or round_number == 6:
                    selection = self.project_dir / "素材选择结果.json"
                    if selection.exists() and self.download_selection_complete(selection):
                        return {"state": "complete", "next_action": "none", "file": str(selection)}
                    return {
                        "state": "ready",
                        "next_action": "download",
                        "message": f"Adaptive search finished after round {round_number}",
                    }

        selection = self.project_dir / "素材选择结果.json"
        if selection.exists() and self.download_selection_complete(selection):
            return {"state": "complete", "next_action": "none", "file": str(selection)}
        return {"state": "ready", "next_action": "download", "message": "All six rounds are complete"}

    def download_selection_complete(self, selection_path: Path) -> bool:
        try:
            selection = read_json_required(selection_path)
        except (FileNotFoundError, json.JSONDecodeError, ReviewError):
            return False
        selected_ids = {item.point_id for item in self.selected_items()}
        point_map = {str(point.get("point_id")): point for point in selection.get("points", [])}
        if not selected_ids.issubset(point_map):
            return False
        for item in self.selected_items():
            if not self.download_point_complete(point_map.get(item.point_id)):
                return False
        return True

    def download_point_complete(self, point: dict[str, Any] | None) -> bool:
        if not point:
            return False
        for download in point.get("downloads", []):
            file_name = str(download.get("file", ""))
            if not file_name:
                return False
            file_path = self.project_dir / str(point.get("chapter_title", "")) / file_name
            if "chapter_number" in point and "point_number" in point:
                chapter_folder = chapter_folder_name(
                    {"chapter_number": point["chapter_number"], "chapter_title": point.get("chapter_title", "")}
                )
                file_path = self.project_dir / chapter_folder / f"{int(point['point_number']):03d}" / file_name
            if not file_path.exists() or file_path.stat().st_size <= 0:
                return False
        return True

    def download_selected(self, batch_size: int = 10) -> Path:
        self.initialize()
        self.set_status("running", "download")
        states = self.review_states(before_round=7, require_complete=True)
        self.validate_search_completion(states)
        candidate_lookup = self.candidate_lookup()
        selection_path = self.project_dir / "素材选择结果.json"
        existing_selection = read_json_required(selection_path) if selection_path.exists() else {}
        existing_points = {
            str(point.get("point_id")): point for point in existing_selection.get("points", [])
        }
        selected_payload: list[dict[str, Any]] = []
        globally_used: set[str] = set()
        processed_this_run = 0

        for item in self.selected_items():
            existing = existing_points.get(item.point_id)
            if self.download_point_complete(existing):
                selected_payload.append(existing)
                globally_used.update(str(value.get("candidate_id")) for value in existing.get("downloads", []))
                continue
            if processed_this_run >= batch_size:
                if existing:
                    selected_payload.append(existing)
                continue
            state = states.get(item.point_id, empty_state())
            ranked = sorted(
                state["reviews"].values(),
                key=lambda value: (-int(value["score"]), int(value["round"]), value["candidate_id"]),
            )
            accepted = [entry for entry in ranked if int(entry["score"]) >= ACCEPTED_SCORE]
            reserves = [entry for entry in ranked if RESERVE_SCORE <= int(entry["score"]) < ACCEPTED_SCORE]
            chosen: list[dict[str, Any]] = []
            for entry in accepted + reserves:
                if entry["candidate_id"] in globally_used:
                    continue
                if entry["candidate_id"] not in candidate_lookup:
                    continue
                chosen.append(entry)
                globally_used.add(entry["candidate_id"])
                if len(chosen) >= FINAL_VIDEOS_PER_POINT:
                    break
            selected_payload.append(self.download_point(item, chosen, candidate_lookup))
            processed_this_run += 1

        total_points = len(self.selected_items())
        complete_points = sum(1 for point in selected_payload if self.download_point_complete(point))
        remaining_points = max(0, total_points - complete_points)
        selection = {
            "schema_version": 3 if self.adaptive_search else (2 if self.plan["mode"] == "fine_timed_storyboards" else 1),
            "mode": self.plan["mode"],
            "search_strategy": self.plan.get("search_strategy", "legacy"),
            "title": self.plan["title"],
            "created_at": utc_now(),
            "accepted_score": ACCEPTED_SCORE,
            "reserve_score": RESERVE_SCORE,
            "download_batch": {
                "batch_size": batch_size,
                "processed_this_run": processed_this_run,
                "total_points": total_points,
                "complete_points": complete_points,
                "remaining_points": remaining_points,
                "complete": remaining_points == 0,
            },
            "points": selected_payload,
        }
        write_json(selection_path, selection)
        if remaining_points == 0:
            self.set_status("completed", "download")
            self.log(f"Download stage finished: {selection_path}")
        else:
            self.set_status("waiting", "download")
            self.log(
                f"Download batch finished: {processed_this_run} point(s), "
                f"{remaining_points} point(s) remaining. Rerun download."
            )
        return selection_path

    def download_point(
        self,
        item: MaterialItem,
        chosen: list[dict[str, Any]],
        candidate_lookup: dict[str, VideoCandidate],
    ) -> dict[str, Any]:
        point_dir = self.project_dir / item.chapter_folder / f"{int(item.point['number']):03d}"
        point_dir.mkdir(parents=True, exist_ok=True)
        downloaded: list[dict[str, Any]] = []
        notes: list[str] = self.api_errors_for_point(item.point_id)
        for index, review in enumerate(chosen, 1):
            candidate = candidate_lookup[review["candidate_id"]]
            file_name = f"video_{index:02d}.mp4"
            file_path = point_dir / file_name
            transfer_status = "dry_run"
            if self.dry_run:
                self.log(f"Dry-run download point {item.point_id} {file_name}: {candidate.candidate_id}")
            else:
                transfer_status = self.download_file(candidate.video_url, file_path, timeout=90)
                if transfer_status == "skipped_existing":
                    self.log(f"Skip existing download point {item.point_id} {file_name}: {candidate.candidate_id}")
                else:
                    self.log(f"Downloaded point {item.point_id} {file_name}: {candidate.candidate_id}")
            status = "合格" if int(review["score"]) >= ACCEPTED_SCORE else "候补（相关性较低）"
            downloaded.append(
                {
                    "file": file_name,
                    "candidate_id": candidate.candidate_id,
                    "source": candidate.source,
                    "author": candidate.author,
                    "page": candidate.page_url,
                    "query": candidate.query,
                    "title": candidate.title,
                    "tags": candidate.tags,
                    "size": f"{candidate.width}x{candidate.height}",
                    "duration": candidate.duration,
                    "score": int(review["score"]),
                    "reason": review["reason"],
                    "round": int(review["round"]),
                    "visual_intent": review.get("visual_intent", "direct"),
                    "status": status,
                    "transfer_status": transfer_status,
                }
            )
        if not downloaded:
            notes.append("没有找到评分达到55分的候选，因此未下载偏题视频。")
        elif len(downloaded) < FINAL_VIDEOS_PER_POINT:
            notes.append(f"合适素材不足：目标3条，实际下载{len(downloaded)}条。")
        if any(item["score"] < ACCEPTED_SCORE for item in downloaded):
            notes.append("使用了55-74分的候补素材，剪辑前请重点复核相关性。")
        self.write_point_note(item, point_dir, downloaded, notes)
        self.write_chapter_note(item)
        result = {
            "point_id": item.point_id,
            "chapter_number": item.chapter_number,
            "chapter_title": item.chapter_title,
            "point_number": int(item.point["number"]),
            "source_text": item.point["source_text"],
            "summary": item.point["summary"],
            "visual_direction": item.point["visual_direction"],
            "downloads": downloaded,
            "notes": notes,
        }
        if "source_start" in item.point:
            result["source_start"] = float(item.point["source_start"])
            result["source_end"] = float(item.point["source_end"])
            result["source_duration"] = round(result["source_end"] - result["source_start"], 6)
        return result

    def search(self, provider: str, query: str) -> list[VideoCandidate]:
        if provider == "Pexels":
            return self.search_pexels(query)
        if provider == "Pixabay":
            return self.search_pixabay(query)
        raise ValueError(f"Unknown provider: {provider}")

    def search_pexels(self, query: str) -> list[VideoCandidate]:
        key = user_environment_value("PEXELS_API_KEY")
        if not key:
            raise RuntimeError("PEXELS_API_KEY is missing")
        params = {"query": query, "orientation": "landscape", "per_page": RAW_RESULTS_PER_PROVIDER}
        url = f"{PEXELS_ENDPOINT}?{urllib.parse.urlencode(params)}"
        data = self.cached_json(
            "pexels", f"pexels|landscape|{RAW_RESULTS_PER_PROVIDER}|{query}", url, {"Authorization": key}
        )
        results: list[VideoCandidate] = []
        for rank, video in enumerate(data.get("videos", []), 1):
            usable = [
                item for item in video.get("video_files", [])
                if int(item.get("width") or 0) > int(item.get("height") or 0)
                and str(item.get("link", "")).lower().split("?")[0].endswith(".mp4")
            ]
            usable.sort(key=lambda item: int(item.get("width") or 0), reverse=True)
            if not usable:
                continue
            source_id = str(video.get("id", ""))
            page_url = str(video.get("url", ""))
            chosen = usable[0]
            pictures = video.get("video_pictures") or []
            thumbnail = str(video.get("image", ""))
            if pictures and pictures[0].get("picture"):
                thumbnail = str(pictures[0]["picture"])
            results.append(
                VideoCandidate(
                    candidate_id=f"pexels:{source_id}", source="Pexels", source_id=source_id,
                    query=query, title=title_from_pexels_url(page_url), tags=[],
                    video_url=str(chosen.get("link", "")), page_url=page_url,
                    thumbnail_url=thumbnail, author=str((video.get("user") or {}).get("name", "")),
                    width=int(chosen.get("width") or 0), height=int(chosen.get("height") or 0),
                    duration=video.get("duration"), api_rank=rank,
                )
            )
        return results

    def search_pixabay(self, query: str) -> list[VideoCandidate]:
        key = user_environment_value("PIXABAY_API_KEY")
        if not key:
            raise RuntimeError("PIXABAY_API_KEY is missing")
        params = {
            "key": key, "q": query, "video_type": "all",
            "orientation": "horizontal", "per_page": RAW_RESULTS_PER_PROVIDER,
        }
        url = f"{PIXABAY_ENDPOINT}?{urllib.parse.urlencode(params)}"
        data = self.cached_json("pixabay", f"pixabay|horizontal|{RAW_RESULTS_PER_PROVIDER}|{query}", url, {})
        results: list[VideoCandidate] = []
        for rank, hit in enumerate(data.get("hits", []), 1):
            files = [(hit.get("videos") or {}).get(name) for name in ("large", "medium", "small", "tiny")]
            usable = [
                item for item in files if isinstance(item, dict) and item.get("url")
                and int(item.get("width") or 0) > int(item.get("height") or 0)
            ]
            usable.sort(key=lambda item: int(item.get("width") or 0), reverse=True)
            if not usable:
                continue
            source_id = str(hit.get("id", ""))
            chosen = usable[0]
            tags = [tag.strip() for tag in str(hit.get("tags", "")).split(",") if tag.strip()]
            results.append(
                VideoCandidate(
                    candidate_id=f"pixabay:{source_id}", source="Pixabay", source_id=source_id,
                    query=query, title=tags[0] if tags else f"Pixabay video {source_id}", tags=tags,
                    video_url=str(chosen.get("url", "")), page_url=str(hit.get("pageURL", "")),
                    thumbnail_url=str(chosen.get("thumbnail", "")), author=str(hit.get("user", "")),
                    width=int(chosen.get("width") or 0), height=int(chosen.get("height") or 0),
                    duration=hit.get("duration"), api_rank=rank,
                )
            )
        return results

    def cached_json(self, source: str, cache_key: str, url: str, headers: dict[str, str]) -> dict[str, Any]:
        cache_file = self.cache_root / source / f"{sha256(cache_key)}.json"
        if cache_file.exists() and datetime.now(timezone.utc) - mtime_utc(cache_file) < timedelta(hours=CACHE_HOURS):
            self.log(f"Cache hit: {source} / {cache_key}")
            return read_json_required(cache_file)
        self.assert_rate_allowed(source)
        self.wait_api_gap()
        request = urllib.request.Request(url, headers={**headers, "User-Agent": "koubo-video-assembler/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = response.read()
                status = response.status
        except urllib.error.HTTPError as exc:
            if exc.code in {401, 403}:
                raise RuntimeError(f"API key invalid or unauthorized ({exc.code})") from exc
            if exc.code == 429:
                raise RuntimeError("API rate limit reached (429); resume later") from exc
            raise RuntimeError(f"HTTP {exc.code}: {exc.reason}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"network error: {exc.reason}") from exc
        self.record_api_request(source)
        if not 200 <= status < 300:
            raise RuntimeError(f"unexpected HTTP status {status}")
        data = json.loads(body.decode("utf-8"))
        write_json(cache_file, data)
        return data

    def wait_api_gap(self) -> None:
        elapsed = time.monotonic() - self.last_api_at
        if elapsed < API_GAP_SECONDS:
            time.sleep(API_GAP_SECONDS - elapsed)
        self.last_api_at = time.monotonic()

    def assert_rate_allowed(self, source: str) -> None:
        limits = RATE_LIMITS[source]
        now = int(time.time())
        entries = read_rate_entries(self.rate_root / f"{source}.log", now - int(limits["window"]))
        if len(entries) >= int(limits["limit"]):
            wait = max(1, entries[0] + int(limits["window"]) - now)
            raise RuntimeError(f"local safety limit reached; resume in about {wait} seconds")

    def record_api_request(self, source: str) -> None:
        path = self.rate_root / f"{source}.log"
        now = int(time.time())
        entries = read_rate_entries(path, now - 86400) + [now]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(str(value) for value in sorted(entries)) + "\n", encoding="utf-8")

    def download_file(self, url: str, output_path: Path, timeout: int) -> str:
        if not url:
            raise RuntimeError("download URL is empty")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if output_path.exists():
            if output_path.is_file() and output_path.stat().st_size > 0:
                return "skipped_existing"
            output_path.unlink(missing_ok=True)
        partial_path = output_path.with_name(output_path.name + ".part")
        partial_path.unlink(missing_ok=True)
        request = urllib.request.Request(url, headers={"User-Agent": "koubo-audio-video-maker/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                if not 200 <= response.status < 300:
                    raise RuntimeError(f"download status {response.status}")
                with partial_path.open("wb") as handle:
                    shutil.copyfileobj(response, handle)
            os.replace(partial_path, output_path)
            return "downloaded"
        except Exception as exc:
            partial_path.unlink(missing_ok=True)
            raise RuntimeError(f"download failed: {exc}") from exc

    def round_search(
        self, item: MaterialItem, round_number: int, dynamic_queries: dict[str, Any]
    ) -> tuple[str, list[str], str]:
        if self.adaptive_search:
            options = item.point["search_options"]
            if round_number in {1, 2}:
                option = options["direct"]
                return option["query"], (["Pexels"] if round_number == 1 else ["Pixabay"]), "direct"
            if round_number == 3:
                option = options["comprehensive"]
                return option["query"], ["Pexels", "Pixabay"], "mixed"
            if round_number == 4:
                option = options["associative"]
                return option["query"], ["Pexels", "Pixabay"], "associative"
            if round_number == 5:
                option = options["metaphorical"]
                return option["query"], ["Pexels", "Pixabay"], "metaphorical"
            dynamic = dynamic_queries.get(item.point_id, {})
            query = str(dynamic.get("query", "")).strip()
            intent = str(dynamic.get("visual_intent", "")).strip()
            if not query or intent not in VISUAL_INTENTS:
                raise ReviewError(f"Point {item.point_id} needs a validated adaptive query for round {round_number}")
            return query, ["Pexels", "Pixabay"], intent
        queries = [str(value).strip() for value in item.point["search_queries"]]
        if round_number == 1:
            return queries[0], ["Pexels"], "direct"
        if round_number == 2:
            return queries[0], ["Pixabay"], "direct"
        if round_number == 3:
            return queries[1] if len(queries) > 1 else queries[0], ["Pexels", "Pixabay"], "direct"
        query = dynamic_queries.get(item.point_id, "").strip()
        if not query:
            raise ReviewError(f"Point {item.point_id} needs a validated query for round {round_number}")
        legacy_intent = "metaphorical" if round_number == 6 else ("associative" if round_number == 5 else "direct")
        return query, ["Pexels", "Pixabay"], legacy_intent

    def is_round_eligible(
        self, item: MaterialItem, round_number: int, states: dict[str, dict[str, Any]]
    ) -> bool:
        state = states.get(item.point_id, empty_state())
        accepted = sum(1 for value in state["reviews"].values() if int(value["score"]) >= ACCEPTED_SCORE)
        usable = sum(1 for value in state["reviews"].values() if int(value["score"]) >= RESERVE_SCORE)
        if self.adaptive_search:
            if round_number == 1:
                return True
            if round_number in {2, 3, 4, 5, 6}:
                return not adaptive_point_complete(state) and not state["had_api_error"]
            return False
        if round_number == 1:
            return True
        if round_number in {2, 3}:
            return accepted < FINAL_VIDEOS_PER_POINT
        if round_number == 4:
            return item.point["priority"] == "high" and accepted == 0 and not state["had_api_error"]
        if round_number in {5, 6}:
            return usable == 0 and not state["had_api_error"]
        return False

    def load_dynamic_queries(
        self, round_number: int, items: list[MaterialItem]
    ) -> dict[str, Any]:
        result_path = self.round_dir(round_number) / "查询结果.json"
        result = read_json_required(result_path)
        if result.get("round") != round_number:
            raise ReviewError(f"Query result round mismatch: {result_path}")
        expected = {item.point_id for item in items}
        points = result.get("points")
        if not isinstance(points, list):
            raise ReviewError(f"Query result points must be an array: {result_path}")
        actual = {point.get("point_id") for point in points if isinstance(point, dict)}
        if actual != expected:
            raise ReviewError(
                f"Round {round_number} query point IDs mismatch; missing={sorted(expected-actual)}, "
                f"extra={sorted(actual-expected)}"
            )
        queries: dict[str, Any] = {}
        for point in points:
            point_id = str(point.get("point_id", ""))
            query = str(point.get("query", "")).strip()
            reason = str(point.get("reason", "")).strip()
            if not query or not query.isascii() or len(query) > 100:
                raise ReviewError(f"Point {point_id} needs an English ASCII query of 1-100 characters")
            if len(reason) < 6:
                raise ReviewError(f"Point {point_id} query reason is too short")
            if self.adaptive_search:
                intent = str(point.get("visual_intent", "")).strip()
                if intent not in VISUAL_INTENTS:
                    raise ReviewError(f"Point {point_id}.visual_intent must be one of {', '.join(VISUAL_INTENTS)}")
                queries[point_id] = {"query": query, "visual_intent": intent}
            else:
                queries[point_id] = query
        return queries

    def load_dynamic_queries_for_status(self, round_number: int) -> dict[str, Any]:
        self.require_review_complete(round_number - 1)
        states = self.review_states(before_round=round_number)
        items = [
            item for item in self.selected_items()
            if self.is_round_eligible(item, round_number, states)
        ]
        return self.load_dynamic_queries(round_number, items)

    def previous_review_context(self, point_id: str, before_round: int) -> dict[str, Any]:
        queries: list[str] = []
        candidates: list[dict[str, Any]] = []
        for round_number in range(1, before_round):
            target_dir = self.round_dir(round_number)
            manifest_path = target_dir / "候选清单.json"
            review_path = target_dir / "AI审核结果.json"
            if not manifest_path.exists() or not review_path.exists():
                continue
            manifest = read_json_required(manifest_path)
            review = read_json_required(review_path)
            review_map = review_entries_by_candidate(review)
            for point in manifest.get("points", []):
                if point.get("point_id") != point_id:
                    continue
                if point.get("query") and point["query"] not in queries:
                    queries.append(point["query"])
                for candidate in point.get("candidates", []):
                    judged = review_map.get(candidate["candidate_id"], {})
                    candidates.append(
                        {
                            "round": round_number,
                            "title": candidate.get("title", ""),
                            "tags": candidate.get("tags", []),
                            "score": judged.get("score"),
                            "reason": judged.get("reason", ""),
                        }
                    )
        return {"queries": queries, "candidates": candidates[-12:]}

    def review_states(
        self, before_round: int, require_complete: bool = False
    ) -> dict[str, dict[str, Any]]:
        states: dict[str, dict[str, Any]] = {}
        for round_number in range(1, before_round):
            target_dir = self.round_dir(round_number)
            manifest_path = target_dir / "候选清单.json"
            review_path = target_dir / "AI审核结果.json"
            if not manifest_path.exists():
                continue
            manifest = read_json_required(manifest_path)
            if manifest.get("status") != "completed":
                raise ReviewError(f"Incomplete collection manifest: {manifest_path}")
            if not review_path.exists():
                if require_complete:
                    raise ReviewError(f"Missing AI review: {review_path}")
                continue
            try:
                self.require_review_complete(round_number)
            except ReviewError:
                if require_complete:
                    raise
                continue
            review = read_json_required(review_path)
            review_map = review_entries_by_candidate(review)
            for point in manifest["points"]:
                state = states.setdefault(point["point_id"], empty_state())
                state["reviewed_rounds"].add(round_number)
                state["had_api_error"] = state["had_api_error"] or bool(point.get("errors"))
                for candidate in point["candidates"]:
                    entry = dict(review_map[candidate["candidate_id"]])
                    entry["round"] = round_number
                    entry["visual_intent"] = point.get("visual_intent", "direct")
                    state["reviews"][candidate["candidate_id"]] = entry
        return states

    def validate_search_completion(self, states: dict[str, dict[str, Any]]) -> None:
        if self.adaptive_search:
            self.require_review_complete(1)
            if not self.adaptive_candidates_complete(states):
                self.require_review_complete(6)
            return
        for item in self.selected_items():
            state = states.get(item.point_id, empty_state())
            rounds = state["reviewed_rounds"]
            if 1 not in rounds:
                raise ReviewError(f"Point {item.point_id} has not completed round 1 review")

            def accepted_through(max_round: int) -> int:
                return sum(
                    1
                    for value in state["reviews"].values()
                    if int(value["round"]) <= max_round and int(value["score"]) >= ACCEPTED_SCORE
                )

            if accepted_through(1) < FINAL_VIDEOS_PER_POINT and 2 not in rounds:
                raise ReviewError(f"Point {item.point_id} still needs round 2 review")
            if accepted_through(2) < FINAL_VIDEOS_PER_POINT and 3 not in rounds:
                raise ReviewError(f"Point {item.point_id} still needs round 3 review")
            needs_round_four = (
                item.point["priority"] == "high"
                and accepted_through(3) == 0
                and not state["had_api_error"]
            )
            if needs_round_four and 4 not in rounds:
                raise ReviewError(f"High-priority point {item.point_id} still needs round 4 review")

            usable_through_four = sum(
                1 for value in state["reviews"].values()
                if int(value["round"]) <= 4 and int(value["score"]) >= RESERVE_SCORE
            )
            if usable_through_four == 0 and 5 not in rounds:
                raise ReviewError(f"Point {item.point_id} still needs round 5 broad-concrete review")
            usable_through_five = sum(
                1 for value in state["reviews"].values()
                if int(value["round"]) <= 5 and int(value["score"]) >= RESERVE_SCORE
            )
            if usable_through_five == 0 and 6 not in rounds:
                raise ReviewError(f"Point {item.point_id} still needs round 6 visual-fallback review")

    def adaptive_candidates_complete(self, states: dict[str, dict[str, Any]]) -> bool:
        return all(
            adaptive_point_complete(states.get(item.point_id, empty_state()))
            for item in self.selected_items()
        )

    def require_review_complete(self, round_number: int) -> None:
        target_dir = self.round_dir(round_number)
        manifest_path = target_dir / "候选清单.json"
        text_path = target_dir / "文字审核结果.json"
        final_path = target_dir / "AI审核结果.json"
        marker_path = target_dir / "审核完成.json"
        manifest = read_json_required(manifest_path)
        if manifest.get("status") != "completed":
            raise ReviewError(f"Round {round_number} collection is incomplete")
        marker = read_json_required(marker_path)
        if marker.get("round") != round_number:
            raise ReviewError(f"Round {round_number} completion marker mismatch")
        expected = {
            "manifest_sha256": file_sha256(manifest_path),
            "text_review_sha256": file_sha256(text_path),
            "final_review_sha256": file_sha256(final_path),
        }
        for field, value in expected.items():
            if marker.get(field) != value:
                raise ReviewError(f"Round {round_number} review changed after review-check; rerun review-check")

    def previous_candidate_ids(self, before_round: int) -> set[str]:
        result: set[str] = set()
        for round_number in range(1, before_round):
            path = self.round_dir(round_number) / "候选清单.json"
            if not path.exists():
                continue
            for point in read_json_required(path).get("points", []):
                result.update(candidate["candidate_id"] for candidate in point.get("candidates", []))
        return result

    def candidate_lookup(self) -> dict[str, VideoCandidate]:
        result: dict[str, VideoCandidate] = {}
        for round_number in range(1, 7):
            path = self.round_dir(round_number) / "候选清单.json"
            if not path.exists():
                continue
            for point in read_json_required(path).get("points", []):
                for candidate in point.get("candidates", []):
                    result[candidate["candidate_id"]] = VideoCandidate.from_dict(candidate)
        return result

    def api_errors_for_point(self, point_id: str) -> list[str]:
        notes: list[str] = []
        for round_number in range(1, 7):
            path = self.round_dir(round_number) / "候选清单.json"
            if not path.exists():
                continue
            for point in read_json_required(path).get("points", []):
                if point.get("point_id") != point_id:
                    continue
                notes.extend(
                    f"第{round_number}轮API错误：{error}" for error in point.get("errors", [])
                )
        return notes

    def write_manifest(self) -> None:
        mode_label = "10秒内语义细分镜" if self.plan["mode"] == "fine_timed_storyboards" else "长视频章节级素材点"
        lines = [
            f"# {self.plan['title']}", "", f"- 模式：{mode_label}",
            f"- 章节数量：{len(self.plan['chapters'])}", f"- 素材点总数：{len(self.items)}",
            "- 流程：分级搜索 → 程序筛选 → AI文字审核 → 必要时看合并缩略图 → 下载",
            "- 每个素材点最终最多下载3条横屏视频", "",
        ]
        for chapter in self.plan["chapters"]:
            folder = chapter_folder_name(chapter)
            lines.extend([f"## {folder} {chapter['chapter_title']}", "", chapter["chapter_summary"], ""])
            timed = self.plan["mode"] == "fine_timed_storyboards"
            if timed:
                lines.append("| 全局编号 | 章内编号 | 时间 | 时长 | 优先级 | 总结 | 主查询 | 次级查询 |")
                lines.append("| ---: | ---: | --- | ---: | --- | --- | --- | --- |")
            else:
                lines.append("| 全局编号 | 章内编号 | 优先级 | 总结 | 主查询 | 次级查询 |")
            lines.append("| ---: | ---: | --- | --- | --- | --- |")
            for item in [value for value in self.items if value.chapter_number == chapter["chapter_number"]]:
                if self.adaptive_search:
                    options = item.point["search_options"]
                    queries = [options["direct"]["query"], options["comprehensive"]["query"]]
                else:
                    queries = item.point["search_queries"]
                secondary = queries[1] if len(queries) > 1 else queries[0]
                if timed:
                    start = float(item.point["source_start"])
                    end = float(item.point["source_end"])
                    lines.append(
                        f"| {item.point_id} | {int(item.point['number']):03d} | {start:.3f}-{end:.3f}s | "
                        f"{end - start:.3f}s | {item.point['priority']} | {one_line(item.point['summary'])} | "
                        f"{queries[0]} | {secondary} |"
                    )
                else:
                    lines.append(
                        f"| {item.point_id} | {int(item.point['number']):03d} | {item.point['priority']} | "
                        f"{one_line(item.point['summary'])} | {queries[0]} | {secondary} |"
                    )
            lines.append("")
        write_text(self.project_dir / "总清单.md", "\n".join(lines).rstrip() + "\n")

    def write_chapter_note(self, item: MaterialItem) -> None:
        path = self.project_dir / item.chapter_folder / "说明.md"
        if not path.exists():
            write_text(path, f"# {item.chapter_folder} {item.chapter_title}\n\n本文件夹按章内素材点编号归档。\n")

    def write_point_note(
        self, item: MaterialItem, point_dir: Path, downloaded: list[dict[str, Any]], notes: list[str]
    ) -> None:
        point = item.point
        lines = [
            f"# {item.point_id} / {item.chapter_title}", "", "## 对应文稿", str(point["source_text"]),
            "", "## 总结", str(point["summary"]), "", "## 优先级", str(point["priority"]),
            "", "## 搜索关键词",
        ]
        if "source_start" in point:
            lines[4:4] = [
                "", "## 分镜时间",
                f"{float(point['source_start']):.3f}s - {float(point['source_end']):.3f}s "
                f"（{float(point['source_end']) - float(point['source_start']):.3f}s）",
            ]
        lines.extend(f"- {value}" for value in point["keywords"])
        lines.extend(["", "## 搜索查询"])
        if self.adaptive_search:
            for style in ("direct", "comprehensive", "associative", "metaphorical"):
                value = point["search_options"][style]
                lines.append(f"- {style}: {value['query']}（{value['reason']}）")
        else:
            lines.extend(f"- {value}" for value in point["search_queries"])
        lines.extend(["", "## 画面建议", str(point["visual_direction"]), "", "## 视频来源"])
        if downloaded:
            for value in downloaded:
                lines.append(
                    f"- {value['file']}：{value['source']}，评分：{value['score']}（{value['status']}），"
                    f"轮次：{value['round']}，查询：{value['query']}，作者：{value['author']}，"
                    f"尺寸：{value['size']}，时长：{value['duration']}，页面：{value['page']}，"
                    f"下载状态：{value.get('transfer_status', 'downloaded')}，审核理由：{value['reason']}"
                )
        else:
            lines.append("暂无合适视频。")
        if notes:
            lines.extend(["", "## 备注"])
            lines.extend(f"- {value}" for value in notes)
        write_text(point_dir / "说明.md", "\n".join(lines) + "\n")


def validate_plan(plan: Any) -> None:
    if not isinstance(plan, dict):
        raise PlanError("root must be an object")
    require_string(plan, "title", "root")
    mode = require_string(plan, "mode", "root")
    if mode not in VALID_PLAN_MODES:
        raise PlanError(f"root.mode must be one of: {', '.join(sorted(VALID_PLAN_MODES))}")
    search_strategy = plan.get("search_strategy", "legacy")
    if search_strategy not in {"legacy", ADAPTIVE_SEARCH_STRATEGY}:
        raise PlanError(f'root.search_strategy must equal "{ADAPTIVE_SEARCH_STRATEGY}" or be omitted')
    chapters = plan.get("chapters")
    if not isinstance(chapters, list) or not chapters:
        raise PlanError("root.chapters must be a non-empty array")
    previous_chapter = 0
    previous_storyboard_end = -1.0
    for chapter_index, chapter in enumerate(chapters, 1):
        path = f"chapters[{chapter_index}]"
        if not isinstance(chapter, dict):
            raise PlanError(f"{path} must be an object")
        number = require_int(chapter, "chapter_number", path)
        if number <= previous_chapter:
            raise PlanError(f"{path}.chapter_number must increase")
        previous_chapter = number
        for field in ("chapter_title", "chapter_summary", "source_text_range"):
            require_string(chapter, field, path)
        points = chapter.get("material_points")
        if not isinstance(points, list) or not points:
            raise PlanError(f"{path}.material_points must be a non-empty array")
        previous_point = 0
        for point_index, point in enumerate(points, 1):
            point_path = f"{path}.material_points[{point_index}]"
            if not isinstance(point, dict):
                raise PlanError(f"{point_path} must be an object")
            point_number = require_int(point, "number", point_path)
            if point_number <= previous_point:
                raise PlanError(f"{point_path}.number must increase")
            previous_point = point_number
            for field in ("source_text", "summary", "visual_direction"):
                require_string(point, field, point_path)
            if mode == "fine_timed_storyboards":
                source_start = require_number(point, "source_start", point_path)
                source_end = require_number(point, "source_end", point_path)
                if source_start < 0:
                    raise PlanError(f"{point_path}.source_start must be >= 0")
                if source_end <= source_start:
                    raise PlanError(f"{point_path}.source_end must be greater than source_start")
                duration = source_end - source_start
                if duration > MAX_STORYBOARD_SECONDS + 0.001:
                    raise PlanError(
                        f"{point_path} duration must be <= {MAX_STORYBOARD_SECONDS:g} seconds; got {duration:.3f}"
                    )
                if source_start < previous_storyboard_end - 0.001:
                    raise PlanError(f"{point_path} overlaps the previous storyboard")
                previous_storyboard_end = source_end
            priority = require_string(point, "priority", point_path)
            if priority not in VALID_PRIORITIES:
                raise PlanError(f"{point_path}.priority must be high, medium, or low")
            require_string_list(point, "keywords", point_path, 1, 8, False)
            if search_strategy == ADAPTIVE_SEARCH_STRATEGY:
                options = point.get("search_options")
                required_styles = {"direct", "comprehensive", "associative", "metaphorical"}
                if not isinstance(options, dict) or set(options) != required_styles:
                    raise PlanError(f"{point_path}.search_options must contain exactly {', '.join(sorted(required_styles))}")
                for style in sorted(required_styles):
                    value = options[style]
                    option_path = f"{point_path}.search_options.{style}"
                    if not isinstance(value, dict):
                        raise PlanError(f"{option_path} must be an object")
                    query = require_string(value, "query", option_path)
                    if not query.isascii() or len(query) > 100:
                        raise PlanError(f"{option_path}.query must be English ASCII and <=100 characters")
                    if len(require_string(value, "reason", option_path)) < 6:
                        raise PlanError(f"{option_path}.reason is too short")
            else:
                queries = require_string_list(point, "search_queries", point_path, 1, 2, True)
                if any(len(query) > 100 for query in queries):
                    raise PlanError(f"{point_path}.search_queries values must be <=100 characters")


def load_and_validate_plan(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"Plan file not found: {path}")
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            plan = json.load(handle)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid strict JSON: {exc}") from exc
    try:
        validate_plan(plan)
    except PlanError as exc:
        raise SystemExit(f"Plan validation failed before API calls: {exc}") from exc
    return plan


def score_candidate(candidate: VideoCandidate, query: str) -> None:
    query_tokens = content_tokens(query)
    metadata = " ".join([candidate.title, *candidate.tags]).lower()
    metadata_tokens = set(tokenize(metadata))
    overlap = query_tokens & metadata_tokens
    coverage = len(overlap) / max(1, len(query_tokens))
    phrase_bonus = 20 if normalize_phrase(query) in normalize_phrase(metadata) else 0
    rank_bonus = max(0, 16 - candidate.api_rank)
    score = min(100, round(64 * coverage + phrase_bonus + rank_bonus))
    reasons = [f"query token coverage {len(overlap)}/{len(query_tokens)}", f"API rank {candidate.api_rank}"]
    for triggers, conflicts, label in CONFLICT_RULES:
        unexpected_conflicts = metadata_tokens & conflicts
        if query_tokens & triggers and unexpected_conflicts and not (query_tokens & conflicts):
            score = max(0, score - 80)
            reasons.append(f"conflict: {label} ({', '.join(sorted(unexpected_conflicts))})")
    candidate.rule_score = score
    candidate.rule_reasons = reasons


def text_review_template(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "round": manifest["round"],
        "instructions": (
            "The LLM/Codex must score each candidate by comparing the narration text, search query, title, tags, "
            "author/page metadata, and visual_direction. Code must not invent or calculate semantic scores. "
            "Fill score (0-100) and reason only. Do not request or perform visual review."
        ),
        "points": [
            {
                "point_id": point["point_id"],
                "visual_intent": point.get("visual_intent", "direct"),
                "reviews": [
                    {
                        "candidate_id": candidate["candidate_id"],
                        "score": None,
                        "reason": "",

                    }
                    for candidate in point["candidates"]
                ],
            }
            for point in manifest["points"]
        ],
    }


def text_review_task(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "round": manifest["round"],
        "instructions": (
            "Judge relevance using visual_intent: direct requires factual visible correspondence; mixed accepts "
            "any defensible direct, associative, or metaphorical fit; associative requires a shared theme or "
            "situation; metaphorical requires a clear symbolic or emotional link, not generic scenery. "
            "Program ranking scores are intentionally hidden."
        ),
        "points": [
            {
                "point_id": point["point_id"],
                "visual_intent": point.get("visual_intent", "direct"),
                "priority": point["priority"],
                "source_text": point["source_text"],
                "summary": point["summary"],
                "visual_direction": point["visual_direction"],
                "query": point["query"],
                "candidates": [
                    {
                        "candidate_id": candidate["candidate_id"],
                        "source": candidate["source"],
                        "title": candidate["title"],
                        "tags": candidate["tags"],
                        "author": candidate["author"],
                        "duration": candidate["duration"],
                        "page_url": candidate["page_url"],
                    }
                    for candidate in point["candidates"]
                ],
            }
            for point in manifest["points"]
        ],
    }


def validate_text_review(manifest: dict[str, Any], review: dict[str, Any]) -> None:
    validate_review_shape(manifest, review, final=False)
    for point in review["points"]:
        for entry in point["reviews"]:
            validate_score(entry, "text review")
            if entry.get("needs_visual_review"):
                raise ReviewError(f"{entry.get('candidate_id')}.needs_visual_review must be false or omitted; visual review is disabled")


def validate_final_review(
    manifest: dict[str, Any], review: dict[str, Any], text_review: dict[str, Any] | None = None
) -> None:
    validate_review_shape(manifest, review, final=True)
    text_map = review_entries_by_candidate(text_review) if text_review else {}
    for point in review["points"]:
        for entry in point["reviews"]:
            validate_score(entry, "AI review")
            basis = entry.get("review_basis")
            if basis != "metadata":
                raise ReviewError(f"{entry.get('candidate_id')}.review_basis must be metadata; visual review is disabled")
            expected = decision_for_score(int(entry["score"]))
            if entry.get("decision") != expected:
                raise ReviewError(
                    f"{entry.get('candidate_id')}.decision must be {expected!r} for score {entry['score']}"
                )


def validate_review_shape(manifest: dict[str, Any], review: dict[str, Any], final: bool) -> None:
    if review.get("round") != manifest.get("round"):
        raise ReviewError("review round does not match candidate manifest")
    manifest_ids = {
        candidate["candidate_id"]
        for point in manifest.get("points", []) for candidate in point.get("candidates", [])
    }
    review_ids = {
        entry.get("candidate_id")
        for point in review.get("points", []) for entry in point.get("reviews", [])
    }
    if manifest_ids != review_ids:
        missing = sorted(manifest_ids - review_ids)
        extra = sorted(review_ids - manifest_ids)
        raise ReviewError(f"review candidate IDs mismatch; missing={missing}, extra={extra}")
    if final:
        manifest_points = {point["point_id"] for point in manifest.get("points", [])}
        review_points = {point.get("point_id") for point in review.get("points", [])}
        if manifest_points != review_points:
            raise ReviewError("AI review point IDs do not match candidate manifest")


def validate_score(entry: dict[str, Any], label: str) -> None:
    score = entry.get("score")
    if not isinstance(score, int) or not 0 <= score <= 100:
        raise ReviewError(f"{entry.get('candidate_id')} score in {label} must be an integer from 0 to 100")
    if not isinstance(entry.get("reason"), str) or len(entry["reason"].strip()) < 6:
        raise ReviewError(f"{entry.get('candidate_id')} reason in {label} must be non-empty")
    lowered = entry["reason"].lower()
    forbidden = ("query token coverage", "api rank", "rule_score", "rule score")
    if any(value in lowered for value in forbidden):
        raise ReviewError(f"{entry.get('candidate_id')} reason copies program ranking language")


def final_review_from_text(review: dict[str, Any]) -> dict[str, Any]:
    score = int(review["score"])
    return {
        "candidate_id": review["candidate_id"],
        "score": score,
        "reason": review["reason"],
        "decision": decision_for_score(score),
        "review_basis": "metadata",
    }


def decision_for_score(score: int) -> str:
    if score >= ACCEPTED_SCORE:
        return "accepted"
    if score >= RESERVE_SCORE:
        return "reserve"
    return "rejected"


def review_entries_by_candidate(review: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        entry["candidate_id"]: entry
        for point in review.get("points", []) for entry in point.get("reviews", [])
    }


def empty_state() -> dict[str, Any]:
    return {"reviews": {}, "had_api_error": False, "reviewed_rounds": set()}


def adaptive_point_complete(state: dict[str, Any]) -> bool:
    scores = [int(value["score"]) for value in state["reviews"].values()]
    usable = sum(1 for score in scores if score >= RESERVE_SCORE)
    accepted = sum(1 for score in scores if score >= ACCEPTED_SCORE)
    return usable >= FINAL_VIDEOS_PER_POINT and accepted >= 1


def flatten_material_items(plan: dict[str, Any]) -> list[MaterialItem]:
    items: list[MaterialItem] = []
    global_number = 1
    for chapter in plan["chapters"]:
        folder = chapter_folder_name(chapter)
        for point in chapter["material_points"]:
            items.append(
                MaterialItem(global_number, int(chapter["chapter_number"]), str(chapter["chapter_title"]), folder, point)
            )
            global_number += 1
    return items


def chapter_folder_name(chapter: dict[str, Any]) -> str:
    number = int(chapter["chapter_number"])
    title = re.sub(r"^(?:第[一二三四五六七八九十百0-9]+幕|导入)[:：]?", "", str(chapter["chapter_title"])).strip()
    return f"{number:02d}-{safe_name(title or str(chapter['chapter_title']))[:40]}"


def title_from_pexels_url(url: str) -> str:
    slug = urllib.parse.urlparse(url).path.rstrip("/").split("/")[-1]
    slug = re.sub(r"-\d+$", "", slug)
    return " ".join(part for part in slug.split("-") if part)


def content_tokens(value: str) -> set[str]:
    return {token for token in tokenize(value) if token not in STOP_WORDS and len(token) > 1}


def tokenize(value: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", value.lower())


def normalize_phrase(value: str) -> str:
    return " ".join(tokenize(value))


def user_environment_value(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if value:
        return value
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                stored, _ = winreg.QueryValueEx(key, name)
                return str(stored).strip()
        except (FileNotFoundError, OSError):
            return ""
    return ""


def require_string(obj: dict[str, Any], field: str, path: str) -> str:
    value = obj.get(field)
    if not isinstance(value, str) or not value.strip():
        raise PlanError(f"{path}.{field} must be a non-empty string")
    return value.strip()


def require_int(obj: dict[str, Any], field: str, path: str) -> int:
    value = obj.get(field)
    if not isinstance(value, int):
        raise PlanError(f"{path}.{field} must be an integer")
    return value


def require_number(obj: dict[str, Any], field: str, path: str) -> float:
    value = obj.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise PlanError(f"{path}.{field} must be a finite number")
    return float(value)


def require_string_list(
    obj: dict[str, Any], field: str, path: str, minimum: int, maximum: int, ascii_only: bool
) -> list[str]:
    value = obj.get(field)
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise PlanError(f"{path}.{field} must contain {minimum}-{maximum} strings")
    result: list[str] = []
    for index, item in enumerate(value, 1):
        if not isinstance(item, str) or not item.strip():
            raise PlanError(f"{path}.{field}[{index}] must be a non-empty string")
        cleaned = item.strip()
        if ascii_only and not cleaned.isascii():
            raise PlanError(f"{path}.{field}[{index}] must be English/ASCII")
        result.append(cleaned)
    return result


def read_json_required(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ReviewError(f"Required file not found: {path}")
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            value = json.load(handle)
    except json.JSONDecodeError as exc:
        raise ReviewError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ReviewError(f"JSON root must be an object: {path}")
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def read_rate_entries(path: Path, cutoff: int) -> list[int]:
    if not path.exists():
        return []
    result: list[int] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            value = int(line.strip())
        except ValueError:
            continue
        if value >= cutoff:
            result.append(value)
    return sorted(result)


def safe_name(value: str) -> str:
    cleaned = "".join("_" if char in '\\/:*?\"<>|' else char for char in value).strip()
    return cleaned or "untitled"


def one_line(value: str) -> str:
    return " ".join(str(value).split()).replace("|", "/")


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def status_result(
    round_number: int, next_action: str, message: str, file_path: str = ""
) -> dict[str, Any]:
    return {
        "state": "waiting",
        "round": round_number,
        "next_action": next_action,
        "message": message,
        "file": file_path,
    }


def mtime_utc(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--plan-path", required=True, type=Path)
    parser.add_argument("--output-root", default="outputs", type=Path)
    parser.add_argument("--start-point", default=1, type=int)
    parser.add_argument("--max-points", type=int)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="Deliberately overwrite an existing stage output")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Low-token supervised video material pipeline")
    subparsers = parser.add_subparsers(dest="command", required=True)
    collect = subparsers.add_parser("collect", help="Collect and rule-filter API candidates")
    add_common_arguments(collect)
    collect.add_argument("--round", required=True, type=int, choices=(1, 2, 3, 4, 5, 6))
    finalize = subparsers.add_parser("finalize-review", help="Convert AI text scoring into final review without visual inspection")
    add_common_arguments(finalize)
    finalize.add_argument("--round", required=True, type=int, choices=(1, 2, 3, 4, 5, 6))
    review_check = subparsers.add_parser("review-check", help="Validate and freeze one completed AI review")
    add_common_arguments(review_check)
    review_check.add_argument("--round", required=True, type=int, choices=(1, 2, 3, 4, 5, 6))
    prepare_queries = subparsers.add_parser("prepare-queries", help="Create dynamic query tasks for rounds 4-6")
    add_common_arguments(prepare_queries)
    prepare_queries.add_argument("--round", required=True, type=int, choices=(4, 5, 6))
    status = subparsers.add_parser("status", help="Report the one valid next workflow action")
    add_common_arguments(status)
    download = subparsers.add_parser("download", help="Download only AI-approved videos")
    add_common_arguments(download)
    download.add_argument("--batch-size", default=10, type=int, help="Download at most this many material points per run")
    validate = subparsers.add_parser("validate", help="Validate the material plan without API calls")
    add_common_arguments(validate)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        pipeline = Pipeline(
            args.plan_path, args.output_root, args.start_point, args.max_points, args.dry_run, args.force
        )
        if args.command == "collect":
            result = pipeline.collect(args.round)
        elif args.command == "finalize-review":
            result = pipeline.finalize_review(args.round)
        elif args.command == "review-check":
            result = pipeline.review_check(args.round)
        elif args.command == "prepare-queries":
            result = pipeline.prepare_queries(args.round)
        elif args.command == "status":
            result = pipeline.workflow_status()
            print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
            return 0
        elif args.command == "download":
            if args.batch_size < 1:
                raise SystemExit("--batch-size must be at least 1")
            result = pipeline.download_selected(args.batch_size)
        else:
            pipeline.initialize()
            result = pipeline.project_dir / "素材分段计划.json"
        print(f"Done: {result}", flush=True)
        return 0
    except Exception as exc:  # noqa: BLE001
        if "pipeline" in locals():
            round_number = getattr(args, "round", None)
            if args.command == "collect" and round_number is not None:
                manifest_path = pipeline.round_dir(round_number) / "候选清单.json"
                if manifest_path.exists():
                    manifest = read_json_required(manifest_path)
                    manifest["status"] = "stopped"
                    manifest["error"] = str(exc)
                    manifest["updated_at"] = utc_now()
                    write_json(manifest_path, manifest)
            current_status = (
                read_json_required(pipeline.status_path) if pipeline.status_path.exists() else {}
            )
            if current_status.get("state") != "stopped" or not current_status.get("point_id"):
                pipeline.set_status("stopped", args.command, round_number, error=str(exc))
            pipeline.log(f"STOPPED {args.command}: {exc}")
        print(f"STOPPED: {exc}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())



