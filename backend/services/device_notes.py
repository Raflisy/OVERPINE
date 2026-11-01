"""Device-specific trivia and original, attributed hardware mottos."""
from __future__ import annotations

import html
import json
import logging
import random
import re
import time
from collections.abc import Mapping
from typing import Any, TypeGuard
from urllib.error import URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

log = logging.getLogger(__name__)

_FACT_CACHE_TTL_SECONDS = 24 * 60 * 60
_MISSING_FACT_CACHE_TTL_SECONDS = 5 * 60
_fact_cache: dict[str, tuple[float, list[dict[str, str | None]]]] = {}
_last_fact_by_device: dict[str, str] = {}
_last_motto_by_device: dict[str, str] = {}
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")

_MOTTOS: dict[str, dict[str, tuple[str, ...]]] = {
    "thinkpad": {
        "wit": (
            "TrackPoint at the ready. Tabs in formation.",
            "Low-key legendary. Very ThinkPad.",
            "Long live ThinkPad users!",
            "Red dot, ready when you are.",
            "The red dot knows the way. Your tabs are another story.",
            "Built for the long haul and the short keyboard shortcut.",
            "One more spreadsheet? The TrackPoint is standing by.",
            "Classic keys, modern-day tab ambitions.",
            "ThinkPad posture: focused. Browser tab count: classified.",
            "A little red dot with a very big to-do list.",
            "Ready for the meeting, the train, and one last document.",
            "The keyboard is ready. The coffee is your department.",
            "Business on the outside, 37 open tabs on the inside.",
            "Point, type, repeat. The ThinkPad way.",
            "Your next idea has a very dependable place to land.",
        ),
        "formal": (
            "A dependable companion for work that moves.",
            "Built for the long haul, ready for the next idea.",
            "A thoughtful balance of dependable design and everyday capability.",
            "Made to support focused work wherever the day leads.",
            "A steady platform for practical ideas and ambitious plans.",
            "Designed around the details that make work feel natural.",
            "A familiar tool for turning plans into progress.",
            "Ready to accompany your work from first draft to final detail.",
            "A considered design for reliable everyday productivity.",
            "Built to make focused work feel effortless.",
        ),
    },
    "yoga": {
        "wit": (
            "One hinge, several good ideas.",
            "Laptop by morning, tiny cinema by evening.",
            "Flip to tent mode. Call it a change of scene.",
            "A little twist can change the whole afternoon.",
            "One laptop, more plot twists than your calendar.",
            "Fold, flip, and find a fresh angle on that task.",
            "In tent mode, even the to-do list gets a new perspective.",
            "A flexible hinge for a day that won't sit still.",
            "Laptop today, presentation station in a quick flip.",
            "Your screen has range. Your playlist is up to you.",
            "One smooth flip away from a different kind of focus.",
        ),
        "formal": (
            "A flexible design for work, reading, and presentation.",
            "Made to move naturally between work and rest.",
            "A versatile format that adapts to the way you work.",
            "Designed for comfortable transitions between tasks.",
            "Flexible form meets practical everyday computing.",
            "A convertible design for a variety of working styles.",
            "Ready to shift perspective as your work changes.",
            "A versatile companion for collaboration and focused time.",
            "Thoughtfully adaptable for work, learning, and creativity.",
        ),
    },
    "macbook": {
        "wit": (
            "All those tabs, and still looking composed.",
            "A polished little multitasker.",
            "Quietly making room for one more window.",
            "Composed on the outside, juggling tabs on the inside.",
            "A smooth operator with a crowded browser.",
            "Making multitasking look suspiciously effortless.",
            "One more window? Certainly, let's keep things interesting.",
            "Quietly turning your to-do list into a done list.",
            "A graceful home for your next big idea.",
        ),
        "formal": (
            "A considered companion for focused everyday work.",
            "Thoughtful hardware for ideas in motion.",
            "Designed to keep your ideas moving throughout the day.",
            "A refined companion for focused work and new possibilities.",
            "Bringing thoughtful design to everyday productivity.",
            "A balanced space for work, communication, and creativity.",
            "Made to support a clear and considered workflow.",
        ),
    },
    "dell": {
        "wit": (
            "Docked or roaming, doing its thing.",
            "Steady machine. Ambitious tab collection.",
            "A little Dell-igence goes a long way.",
            "Docked, undocked, and still looking for that one cable.",
            "A dependable machine with a very adventurous task list.",
            "Quietly keeping all those windows in line.",
            "Ready for the next call and the next 12 browser tabs.",
            "Serious about work. Curious about every open window.",
            "A steady presence in a very busy desktop.",
        ),
        "formal": (
            "A steady partner for the work ahead.",
            "Ready for thoughtful work, wherever it happens.",
            "A practical foundation for focused work and new ideas.",
            "Designed to keep your day moving with confidence.",
            "Dependable computing for work at your own pace.",
            "Ready for the details, the deadlines, and what comes next.",
            "A considered tool for productive days.",
        ),
    },
    "hp": {
        "wit": (
            "Powered up and only slightly outnumbered by tabs.",
            "A handy machine with a big to-do list.",
            "The to-do list is long. At least the battery has plans.",
            "Ready to help, even if the tabs have formed a committee.",
            "A capable helper for a day full of little tasks.",
            "One more window? This machine believes in you.",
            "Keeping your projects together, one window at a time.",
            "Powered up for the next bright idea.",
        ),
        "formal": (
            "A practical partner for everyday projects.",
            "Designed to keep your work moving.",
            "A reliable start for the ideas you want to explore.",
            "Designed for the rhythm of everyday work.",
            "A practical companion for work, learning, and discovery.",
            "Ready to support the tasks that move your day forward.",
        ),
    },
    "surface": {
        "wit": (
            "A touch of class; a whole lot of tabs.",
            "A flexible screen for a very fixed number of meetings.",
            "Touchscreen ready. Fingerprint smudges included.",
            "A little tap, a little type, and a lot of ambition.",
            "The screen is touch-friendly. The calendar is less so.",
            "Pen, touch, keyboard: choose your own productivity adventure.",
            "A screen that listens to your fingers better than your inbox.",
        ),
        "formal": (
            "A versatile canvas for work and creative thought.",
            "Ready to adapt to the shape of your day.",
            "A flexible platform for focused work and creative exploration.",
            "Designed to bring touch and traditional computing together.",
            "A versatile companion for work in many forms.",
            "Adaptable design for ideas, notes, and everyday tasks.",
        ),
    },
    "asus": {
        "wit": (
            "Ready for the render, the raid, or the recipe.",
            "Doing serious work with unserious numbers of tabs.",
            "Ready for the big render and the tiny side quest.",
            "A capable machine with a suspiciously large tab collection.",
            "One more project? Let's make it interesting.",
            "Working hard while your playlist does the heavy lifting.",
            "Built for ambition, currently hosting a snack search.",
            "Rendering the future, one open window at a time.",
        ),
        "formal": (
            "A capable companion for focused work and play.",
            "Set for the next project, wherever it leads.",
            "Designed to support demanding projects and creative work.",
            "A versatile platform for productivity and play.",
            "Ready for focused tasks and ambitious ideas.",
            "A capable companion for the work you choose to pursue.",
        ),
    },
    "acer": {
        "wit": (
            "Acer-iously ready for another open window.",
            "One more task? This machine is game.",
            "Acer-iously good at keeping the day moving.",
            "One more tab won't hurt. Probably.",
            "Ready for the next task and the next good idea.",
            "Quietly making progress while your tabs multiply.",
            "A reliable little boost for the daily to-do list.",
            "All systems go; snacks are still user-provided.",
        ),
        "formal": (
            "A reliable companion for the day ahead.",
            "Made to carry your ideas from one place to another.",
            "Designed for dependable work wherever you go.",
            "A practical companion for everyday productivity.",
            "Ready to take your projects from idea to completion.",
            "Built to support work and discovery throughout the day.",
        ),
    },
    "lenovo": {
        "wit": (
            "Your reliable sidekick, now accepting more tabs.",
            "A little Lenovo, a lot on the to-do list.",
            "Dependable by design, curious by browser history.",
            "Ready for the next task and the next thoughtful break.",
            "A sturdy sidekick for ideas big and small.",
            "Keeping your work moving, one click at a time.",
            "Reliable hardware, ambitious plans.",
            "Here for the deadline and the detour to check one thing.",
            "A practical partner for a very creative tab arrangement.",
            "Steady hands, busy desktop.",
        ),
        "formal": (
            "A reliable companion for work and everyday discovery.",
            "Thoughtfully built for the work you bring along.",
            "Designed to support productive work throughout your day.",
            "A dependable platform for everyday ideas and tasks.",
            "Thoughtful computing for work that moves with you.",
            "A practical companion for focused work and exploration.",
            "Ready to make room for the next idea.",
            "Built to keep your workflow steady and your options open.",
        ),
    },
}

_DEFAULT_MOTTOS: dict[str, tuple[str, ...]] = {
    "wit": (
        "Small machine, big plans.",
        "Fans low, spirits high.",
        "Quietly getting things done.",
        "Powered on and ready for one more idea.",
        "A calm desktop in a world of busy tabs.",
        "Doing its best impression of a very organized assistant.",
        "Your next task has entered the chat.",
        "Plenty of computing power; still can't find that file for you.",
        "Working quietly while the tabs hold a meeting.",
        "All set for the task you just remembered.",
        "A little machine with excellent follow-through.",
        "Current status: ready for whatever you open next.",
        "Keeping cool, even when the schedule isn't.",
        "Your ideas are in good company.",
        "The fan is quiet. The to-do list is not.",
        "Built for the plan, prepared for the plot twist.",
        "Doing the work behind the scenes, as usual.",
        "Still faster than deciding which tab to close.",
        "Ready when you are. No dramatic boot-up monologue.",
        "A tidy little engine for untidy human plans.",
    ),
    "formal": (
        "A thoughtful companion for the work ahead.",
        "Ready for the next idea.",
        "A dependable platform for work, learning, and creativity.",
        "Designed to support your ideas from start to finish.",
        "A capable companion for the tasks that matter to you.",
        "Ready to help turn careful plans into useful work.",
        "A thoughtful balance of capability and everyday comfort.",
        "Built for the moments when an idea becomes a project.",
        "A steady tool for focused work and open-ended curiosity.",
        "Here to support how you choose to think and create.",
        "A reliable starting point for whatever comes next.",
        "Designed for the everyday work that adds up to progress.",
        "A quiet partner in learning, making, and getting things done.",
        "Ready to support a new question or a familiar routine.",
        "A considered companion for productive days.",
    ),
}


def _read_json(url: str) -> dict[str, Any]:
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "OVERPINE/0.1 (local device fact lookup)",
        },
    )
    with urlopen(request, timeout=3) as response:
        payload: Any = json.loads(response.read().decode("utf-8"))
    if not _is_json_object(payload):
        raise ValueError("Wikipedia returned an unexpected response.")
    return payload


def _is_json_object(value: Any) -> TypeGuard[dict[str, Any]]:
    return isinstance(value, dict)


def _is_json_array(value: Any) -> TypeGuard[list[Any]]:
    return isinstance(value, list)


def _lookup_wikipedia_facts(
    manufacturer: str, model: str
) -> list[dict[str, str | None]]:
    query = " ".join(
        value.strip()
        for value in (manufacturer, model)
        if value.strip() and value.strip().casefold() != "not detected"
    )
    if not query:
        return [{
            "fact": None,
            "source": None,
            "source_url": None,
            "message": "A device model was not available for the online fact lookup.",
        }]

    search_url = "https://en.wikipedia.org/w/api.php?" + urlencode(
        {
            "action": "query",
            "list": "search",
            "srsearch": query,
            "srlimit": 5,
            "format": "json",
        }
    )
    search_data = _read_json(search_url)
    query_data = search_data.get("query")
    if not _is_json_object(query_data):
        raise ValueError("Wikipedia returned an unexpected search response.")
    search_results = query_data.get("search", [])
    if not _is_json_array(search_results):
        raise ValueError("Wikipedia returned an unexpected search response.")

    manufacturer_terms = {
        term.casefold()
        for term in re.findall(r"[a-zA-Z0-9]+", manufacturer)
        if len(term) > 1
    }
    model_terms = {
        term.casefold()
        for term in re.findall(r"[a-zA-Z0-9]+", model)
        if len(term) > 1
    } - manufacturer_terms
    model_identifier_terms = {
        term
        for term in model_terms
        if re.search(r"[a-z]", term) and re.search(r"[0-9]", term)
    }
    candidates: list[dict[str, str]] = []
    for item in search_results:
        if _is_json_object(item) and isinstance(item.get("title"), str):
            title = item["title"]
            snippet = item.get("snippet", "")
            candidates.append(
                {
                    "title": title,
                    "snippet": snippet if isinstance(snippet, str) else "",
                }
            )

    def title_score(candidate: dict[str, str]) -> int:
        title_terms = {
            term.casefold()
            for term in re.findall(r"[a-zA-Z0-9]+", candidate["title"])
        }
        snippet = re.sub(
            r"<[^>]*>",
            " ",
            html.unescape(candidate["snippet"]),
        )
        snippet_terms = {
            term.casefold() for term in re.findall(r"[a-zA-Z0-9]+", snippet)
        }
        if model_identifier_terms and not model_identifier_terms.intersection(
            title_terms | snippet_terms
        ):
            return 0
        return len(model_terms & title_terms)

    candidates.sort(key=title_score, reverse=True)
    candidates = [candidate for candidate in candidates if title_score(candidate) > 0]
    if not candidates:
        return [{
            "fact": None,
            "source": None,
            "source_url": None,
            "message": "No matching device fact was found on Wikipedia.",
        }]

    facts: list[dict[str, str | None]] = []
    seen_facts: set[str] = set()
    for candidate in candidates[:3]:
        title = candidate["title"]
        summary_url = (
            "https://en.wikipedia.org/api/rest_v1/page/summary/"
            + quote(title.replace(" ", "_"), safe="_()")
        )
        summary = _read_json(summary_url)
        extract = summary.get("extract")
        if not isinstance(extract, str) or not extract.strip():
            continue

        sentences = [
            sentence.strip()
            for sentence in _SENTENCE_BOUNDARY.split(extract.strip())
            if sentence.strip()
        ]
        for fact in sentences:
            if len(fact) > 280:
                fact = fact[:277].rsplit(" ", 1)[0].rstrip(" ,;:") + "..."
            fact_key = fact.casefold()
            if fact_key in seen_facts:
                continue
            seen_facts.add(fact_key)
            facts.append({
                "fact": fact,
                "source": f"Wikipedia · {title}",
                "source_url": f"https://en.wikipedia.org/wiki/{quote(title.replace(' ', '_'), safe='_()')}",
                "message": None,
            })

    return facts or [{
        "fact": None,
        "source": None,
        "source_url": None,
        "message": "No matching device fact was found on Wikipedia.",
    }]


def _lookup_wikipedia_fact(
    manufacturer: str, model: str
) -> dict[str, str | None]:
    """Return one random fact while preserving the original single-fact helper."""
    facts = _lookup_wikipedia_facts(manufacturer, model)
    available_facts = [fact for fact in facts if fact["fact"]]
    return random.choice(available_facts) if available_facts else facts[0]


def _device_fact(manufacturer: str, model: str) -> dict[str, str | None]:
    cache_key = f"{manufacturer.strip().casefold()}|{model.strip().casefold()}"
    now = time.monotonic()
    cached = _fact_cache.get(cache_key)
    if cached:
        cache_ttl = (
            _FACT_CACHE_TTL_SECONDS
            if any(fact["fact"] for fact in cached[1])
            else _MISSING_FACT_CACHE_TTL_SECONDS
        )
        if now - cached[0] < cache_ttl:
            facts = cached[1]
        else:
            facts = []
    else:
        facts = []

    if not facts:
        try:
            facts = _lookup_wikipedia_facts(manufacturer, model)
        except (OSError, TimeoutError, URLError, ValueError) as error:
            log.warning("Online device fact lookup failed: %s", error)
            facts = [{
                "fact": None,
                "source": None,
                "source_url": None,
                "message": "Wikipedia could not be reached. Your detected specs remain available.",
            }]
        _fact_cache[cache_key] = (
            now,
            [fact.copy() for fact in facts],
        )

    available_facts = [fact for fact in facts if fact["fact"]]
    if not available_facts:
        return facts[0].copy()
    previous_fact = _last_fact_by_device.get(cache_key)
    new_facts = [
        fact for fact in available_facts if fact["fact"] != previous_fact
    ]
    selected_fact = random.choice(new_facts or available_facts)
    _last_fact_by_device[cache_key] = str(selected_fact["fact"])
    return selected_fact.copy()


def _device_motto(manufacturer: str, model: str) -> dict[str, str]:
    device = f"{manufacturer} {model}".casefold()
    matched_key = next(
        (
            key
            for key in (
                "thinkpad",
                "yoga",
                "macbook",
                "surface",
                "dell",
                "hp",
                "asus",
                "acer",
                "lenovo",
            )
            if key in device
        ),
        None,
    )
    category = matched_key.title() if matched_key else "Device"
    style_options = _MOTTOS[matched_key] if matched_key else _DEFAULT_MOTTOS
    motto_options = [
        (style, text)
        for style, texts in style_options.items()
        for text in texts
    ]
    cache_key = f"{manufacturer.strip().casefold()}|{model.strip().casefold()}"
    previous_motto = _last_motto_by_device.get(cache_key)
    new_mottos = [
        option for option in motto_options if option[1] != previous_motto
    ]
    style, text = random.choice(new_mottos or motto_options)
    _last_motto_by_device[cache_key] = text

    return {
        "text": text,
        "attribution": f"OVERPINE · original {category} {style}",
    }


def get_device_note(system: Mapping[str, object]) -> dict[str, str | None]:
    """Return a sourced device fact and an original motto for detected hardware."""
    manufacturer = str(system.get("manufacturer") or "")
    model = str(system.get("device_model") or "")
    fact = _device_fact(manufacturer, model)
    motto = _device_motto(manufacturer, model)
    return {
        **fact,
        "motto": motto["text"],
        "motto_attribution": motto["attribution"],
    }
