"""Render a sample digest from built-in fixture data — no network, no API key.

Used to produce docs/assets/digest.png. Everything is fictional sample content
(outlets live on example.* domains); the "translated" fields are what the
enrich stage would have written, so the page shows the post-LLM layout.

    poetry run python scripts/demo_digest.py --home /tmp/demo   # prints the HTML path
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

_NOW = datetime(2026, 11, 15, 7, 0, 0)

# (topic, languages, [articles]); each article: dict of Article fields + "extra_sources"
_TOPICS: list[tuple[str, list[str], list[dict[str, object]]]] = [
    (
        "renewable energy",
        ["en", "de"],
        [
            {
                "title_original": "Offshore-Windpark in der Nordsee speist erstmals Strom ins Netz",
                "title_translated": (
                    "North Sea offshore wind farm feeds power into the grid for the first time"
                ),
                "language_original": "de",
                "source_name": "Beispiel Zeitung",
                "source_url": "https://www.example.de/energie/windpark-netz",
                "summary": (
                    "The first turbines of a new offshore wind farm have been connected to the "
                    "grid, months ahead of schedule. Operators expect full output next year, "
                    "enough to supply several hundred thousand homes."
                ),
                "llm_reason": "Milestone for a large project; reported by three outlets.",
                "extra_sources": ["example.org", "example.net"],
                "hours_ago": 5,
            },
            {
                "title_original": "Grid-scale battery prices fall for a third straight year",
                "language_original": "en",
                "source_name": "Sample Energy Wire",
                "source_url": "https://energy.example.com/battery-prices",
                "summary": (
                    "An annual industry survey finds average pack prices for stationary "
                    "storage fell again, driven by cheaper cell chemistries and larger "
                    "factories. Analysts say it strengthens the case for solar-plus-storage."
                ),
                "llm_reason": "Clear trend with numbers; useful context.",
                "hours_ago": 20,
            },
        ],
    ),
    (
        "space exploration",
        ["en", "fr"],
        [
            {
                "title_original": (
                    "La sonde européenne transmet ses premières images de l'astéroïde"
                ),
                "title_translated": "European probe sends back its first images of the asteroid",
                "language_original": "fr",
                "source_name": "Le Journal Exemple",
                "source_url": "https://www.example.fr/espace/sonde-images",
                "summary": (
                    "Mission controllers released the first close-range images from the "
                    "probe's approach. The pictures will be used to map the surface before "
                    "the spacecraft settles into orbit."
                ),
                "llm_reason": "First data from the mission's main science phase.",
                "extra_sources": ["example.com"],
                "hours_ago": 9,
            },
            {
                "title_original": "Same rocket booster flies and lands for the tenth time",
                "language_original": "en",
                "source_name": "Sample Space News",
                "source_url": "https://space.example.org/booster-ten",
                "summary": (
                    "A first-stage booster completed its tenth launch and landing, a new "
                    "record for its family. Reuse at this rate is lowering the cost of "
                    "putting small satellites into orbit."
                ),
                "llm_reason": "Record for reusability.",
                "hours_ago": 30,
            },
        ],
    ),
    (
        "open source software",
        ["en"],
        [
            {
                "title_original": "Popular Python web framework ships a long-term support release",
                "language_original": "en",
                "source_name": "Example Dev Weekly",
                "source_url": "https://dev.example.net/framework-lts",
                "summary": (
                    "The new LTS release will get security fixes for three years. It drops "
                    "support for old Python versions and makes async views the default."
                ),
                "llm_reason": "Affects many production deployments.",
                "hours_ago": 14,
            },
            {
                "title_original": "Kernel maintainers debate the next batch of Rust drivers",
                "language_original": "en",
                "source_name": "Sample Linux Journal",
                "source_url": "https://linux.example.com/rust-drivers",
                "headline_only": True,
                "hours_ago": 26,
            },
        ],
    ),
    (
        "F1",
        ["en"],
        [
            {
                "title_original": "Title fight goes to the final race after wet qualifying",
                "language_original": "en",
                "source_name": "Example Motorsport",
                "source_url": "https://racing.example.org/title-decider",
                "summary": (
                    "Rain shuffled the grid in qualifying, leaving the two championship "
                    "contenders side by side on the third row. The title will be decided at "
                    "the season finale."
                ),
                "llm_reason": "Decides the championship.",
                "extra_sources": ["example.com", "example.net"],
                "hours_ago": 3,
            },
        ],
    ),
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, help="data home to use (default: a temp dir)")
    args = parser.parse_args()
    home = args.home or Path(tempfile.mkdtemp(prefix="digest-demo-"))
    # Must be set before newsfold is imported — paths resolve at import time.
    os.environ["NEWSFOLD_HOME"] = str(home)
    os.environ["NEWSFOLD_CONFIG_DIR"] = str(home / "config")  # packaged defaults only
    os.environ.pop("NEWSFOLD_DB_URL", None)

    from newsfold.db import session_scope, upgrade_db
    from newsfold.digest.render import write_digest
    from newsfold.llm.usage import UsageLog
    from newsfold.llm_usage import flush_usage
    from newsfold.models import (
        Article,
        ArticleStatus,
        Run,
        SourceType,
        Topic,
        TopicMode,
        TopicStatus,
    )

    db_file = home / "data" / "newsfold.db"
    if db_file.exists():
        db_file.unlink()
    upgrade_db()

    with session_scope() as session:
        run = Run(started_at=_NOW, finished_at=_NOW)
        session.add(run)
        session.flush()
        usage = UsageLog()
        n = 0
        for keyword, languages, articles in _TOPICS:
            topic = Topic(
                keyword=keyword,
                mode=TopicMode.temporary if keyword == "F1" else TopicMode.persistent,
                languages=json.dumps(languages),
                status=TopicStatus.active,
            )
            session.add(topic)
            session.flush()
            for rank, spec in enumerate(articles, start=1):
                n += 1
                cluster = f"demo-{n}"
                published = _NOW - timedelta(hours=int(str(spec.get("hours_ago", 1))))
                headline_only = bool(spec.get("headline_only"))
                session.add(
                    Article(
                        topic_id=topic.id,
                        source_url=str(spec["source_url"]),
                        source_name=str(spec["source_name"]),
                        source_type=SourceType.google_news,
                        title_original=str(spec["title_original"]),
                        title_translated=spec.get("title_translated"),
                        language_original=str(spec["language_original"]),
                        summary=spec.get("summary"),
                        llm_rank=rank,
                        llm_reason=spec.get("llm_reason"),
                        rank_score=10.0 - rank,
                        cluster_id=cluster,
                        headline_only=headline_only,
                        status=(
                            ArticleStatus.clustered if headline_only else ArticleStatus.processed
                        ),
                        published_at=published,
                    )
                )
                for i, domain in enumerate(spec.get("extra_sources", []) or []):  # type: ignore[union-attr]
                    session.add(
                        Article(
                            topic_id=topic.id,
                            source_url=f"https://{domain}/story-{n}-{i}",
                            source_name=str(domain),
                            source_type=SourceType.google_news,
                            title_original=str(spec["title_original"]),
                            status=ArticleStatus.decoded,
                            cluster_id=cluster,
                            published_at=published,
                        )
                    )
                if not headline_only:
                    usage.record(
                        stage="enrich",
                        provider="ollama",
                        model="llama3.1",
                        prompt_tokens=1400 + 37 * n,
                        completion_tokens=310 + 11 * n,
                    )
            usage.record(
                stage="rank",
                provider="ollama",
                model="llama3.1",
                prompt_tokens=900 + 50 * len(articles),
                completion_tokens=120,
            )
        flush_usage(session, usage, run.id)

    with session_scope() as session:
        path = write_digest(session, fmt="html")
    print(path)


if __name__ == "__main__":
    main()
