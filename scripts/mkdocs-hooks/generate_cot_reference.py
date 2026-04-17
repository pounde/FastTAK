"""MkDocs hook: generate CoT type reference page from CoTtypes.xml.

Runs on_pre_build to produce docs/reference/cot-types.md from the XML
registry before MkDocs reads the docs directory.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

log = logging.getLogger("mkdocs.hooks.cot_reference")

# Position labels from types.txt
EVENT_TYPES = {
    "a": ("Atoms", "Actual physical entities — people, vehicles, aircraft, structures"),
    "b": (
        "Bits",
        "Meta-information about data sources — imagery, sensor data, routes, mappings",
    ),
    "c": (
        "Capability",
        "Applied to an area — surveillance, rescue, fires, logistics, communications",
    ),
    "r": (
        "Reservation/Restriction",
        "Area notices — unsafe zones, contamination, flight restrictions",
    ),
    "t": (
        "Tasking",
        "Requests and orders for service — surveillance, strike, mensurate",
    ),
    "u": (
        "Map",
        "User-drawn map objects — drawings, shapes, range and bearing, obstacles",
    ),
    "y": ("Reply", "Responses to tasking — ack, status, completion, failure"),
}

AFFILIATIONS = {
    "p": "Pending",
    "u": "Unknown",
    "a": "Assumed Friend",
    "f": "Friend",
    "n": "Neutral",
    "s": "Suspect",
    "h": "Hostile",
    "j": "Joker",
    "k": "Faker",
    "o": "None Specified",
    "x": "Other (DEPRECATED)",
    ".": "Wildcard (any affiliation)",
}

BATTLE_DIMENSIONS = {
    "P": "Space",
    "A": "Air",
    "G": "Ground",
    "S": "Sea Surface",
    "U": "Sea Subsurface",
    "X": "Other",
}


def parse_registry(path: Path) -> list[dict]:
    """Parse CoTtypes.xml into a list of entry dicts."""
    tree = ET.parse(path)
    entries = []
    for elem in tree.iter("cot"):
        code = elem.get("cot", "").strip()
        if not code:
            continue
        entries.append(
            {
                "code": code,
                "full": elem.get("full", "").strip(),
                "desc": elem.get("desc", "").strip(),
                "source": elem.get("source", "").strip(),
                "notes": elem.get("notes", "").strip(),
            }
        )
    return entries


def build_tree(entries: list[dict]) -> dict:
    """Build a nested dict tree from full paths for grouping."""
    tree: dict = {}
    for entry in entries:
        full = entry["full"]
        if not full:
            continue
        parts = full.split("/")
        if len(parts) >= 2:
            key = f"{parts[0]}/{parts[1]}"
        else:
            key = parts[0]
        if key not in tree:
            tree[key] = []
        tree[key].append(entry)
    return tree


def write_decoder(lines: list[str]) -> None:
    """Write the decoder section explaining how to read CoT types."""
    lines.append("## How to Read CoT Type Codes\n")
    lines.append("CoT type codes are dash-separated hierarchical strings. Each position")
    lines.append("narrows the type from general to specific:\n")
    lines.append("```")
    lines.append("a  -  f  -  G  -  U  -  C  -  I")
    lines.append("│     │     │     │     │     └─ Infantry")
    lines.append("│     │     │     │     └─ Combat")
    lines.append("│     │     │     └─ Unit")
    lines.append("│     │     └─ Ground")
    lines.append("│     └─ Friendly")
    lines.append("└─ Atom (physical entity)")
    lines.append("```\n")
    lines.append("The `.` in registry entries (e.g., `a-.-G`) is a wildcard — the type")
    lines.append("applies to any affiliation. On the wire, substitute the actual affiliation:")
    lines.append("`a-f-G` (friendly), `a-h-G` (hostile), etc.\n")

    # Position 1
    lines.append('??? info "Position 1: Event Type"\n')
    lines.append("    | Code | Name | Description |")
    lines.append("    |------|------|-------------|")
    for code in sorted(EVENT_TYPES):
        name, desc = EVENT_TYPES[code]
        lines.append(f"    | `{code}` | {name} | {desc} |")
    lines.append("")

    # Position 2
    lines.append('??? info "Position 2: Affiliation (atoms only)"\n')
    lines.append("    | Code | Meaning |")
    lines.append("    |------|---------|")
    for code in ["f", "h", "n", "u", "a", "p", "s", "j", "k", "o", "."]:
        lines.append(f"    | `{code}` | {AFFILIATIONS[code]} |")
    lines.append("")

    # Position 3
    lines.append('??? info "Position 3: Battle Dimension (atoms only)"\n')
    lines.append("    | Code | Meaning |")
    lines.append("    |------|---------|")
    for code in sorted(BATTLE_DIMENSIONS):
        lines.append(f"    | `{code}` | {BATTLE_DIMENSIONS[code]} |")
    lines.append("")


def write_registry(lines: list[str], entries: list[dict]) -> None:
    """Write the type registry with collapsible sections."""
    lines.append("## Type Registry\n")

    tree = build_tree(entries)

    type_order = {
        "Atoms": 0,
        "Bits": 1,
        "Capability": 2,
        "Reservation-Restriction": 3,
        "Tasking": 4,
        "Map": 5,
        "Reply": 6,
    }

    def sort_key(group_name):
        top = group_name.split("/")[0]
        return (type_order.get(top, 99), group_name)

    for group_name in sorted(tree.keys(), key=sort_key):
        group_entries = tree[group_name]

        if len(group_entries) > 80:
            lines.append(f'\n??? info "{group_name} — {len(group_entries)} types"\n')
            subgroups: dict[str, list[dict]] = defaultdict(list)
            for entry in group_entries:
                parts = entry["full"].split("/")
                if len(parts) >= 3:
                    subkey = parts[2]
                else:
                    subkey = "(General)"
                subgroups[subkey].append(entry)

            for subkey in sorted(subgroups):
                sub_entries = subgroups[subkey]
                lines.append(f'    ??? info "{subkey} — {len(sub_entries)} types"\n')
                lines.append("        | Code | Description | Source |")
                lines.append("        |------|-------------|--------|")
                for e in sorted(sub_entries, key=lambda x: x["code"]):
                    lines.append(f"        | `{e['code']}` | {e['desc']} | {e['source']} |")
                lines.append("")
        else:
            lines.append(f'\n??? info "{group_name} — {len(group_entries)} types"\n')
            lines.append("    | Code | Description | Source |")
            lines.append("    |------|-------------|--------|")
            for e in sorted(group_entries, key=lambda x: x["code"]):
                lines.append(f"    | `{e['code']}` | {e['desc']} | {e['source']} |")
            lines.append("")


def generate(docs_dir: Path) -> None:
    """Generate the CoT type reference markdown."""
    xml_path = docs_dir / "reference" / "cot" / "CoTtypes.xml"
    output_path = docs_dir / "reference" / "cot-types.md"

    if not xml_path.exists():
        log.warning("CoTtypes.xml not found at %s, skipping generation", xml_path)
        return

    entries = parse_registry(xml_path)
    log.info("Parsed %d CoT type entries", len(entries))

    lines: list[str] = []

    lines.append("# CoT Type Codes\n")
    lines.append("A reference for Cursor-on-Target (CoT) type codes used in TAK systems.")
    lines.append("This is a best-effort compilation — not an official standard. See")
    lines.append("`docs/reference/cot/CoTtypes.xml` for the source registry with full")
    lines.append("provenance and traceability.\n")

    write_decoder(lines)
    write_registry(lines, entries)

    lines.append("\n---\n")
    lines.append("*Generated at build time from the FastTAK CoT Type Registry")
    lines.append("(2026 Consolidated Edition).*")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log.info("Generated %s", output_path.relative_to(docs_dir.parent))


def on_pre_build(config, **kwargs):
    """MkDocs hook: generate CoT reference before build."""
    docs_dir = Path(config["docs_dir"])
    generate(docs_dir)
