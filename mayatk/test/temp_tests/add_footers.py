"""One-shot: add Footer widget + customwidget to mayatk .ui files missing one.

Strategy: text-based insertion to preserve original formatting/diff size.

For each target UI:
- If file already contains class="Footer", skip.
- Find the layout that contains the Header widget. Insert Footer item as the
  last item of that layout. (Falls back to outermost <layout> child of <widget>
  for UIs without a Header — currently only env_utils/workspace_map.ui.)
- Add Footer to <customwidgets>, creating that section if needed.
"""
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path("o:/Cloud/Code/_scripts/mayatk/mayatk")

UI_FILES = [
    "edit_utils/naming/naming.ui",
    "edit_utils/dynamic_pipe.ui",
    "env_utils/scene_exporter/scene_exporter.ui",
    "env_utils/hierarchy_manager/hierarchy_manager.ui",
    "env_utils/workspace_map.ui",
    "light_utils/hdr_manager.ui",
    "mat_utils/mat_updater.ui",
    "mat_utils/shader_templates/shader_templates.ui",
    "mat_utils/game_shader.ui",
    "display_utils/color_manager.ui",
    "display_utils/exploded_view.ui",
    "nurbs_utils/image_tracer.ui",
    "rig_utils/telescope_rig.ui",
    "rig_utils/shadow_rig.ui",
    "rig_utils/tube_rig.ui",
    "rig_utils/wheel_rig.ui",
    "uv_utils/rizom_bridge/rizom_bridge.ui",
    "edit_utils/bevel.ui",
    "edit_utils/bridge.ui",
    "edit_utils/mirror.ui",
    "edit_utils/snap.ui",
    "edit_utils/duplicate_grid.ui",
    "edit_utils/duplicate_linear.ui",
    "edit_utils/duplicate_radial.ui",
    "ui_utils/calculator.ui",
]

FOOTER_CUSTOMWIDGET = """  <customwidget>
   <class>Footer</class>
   <extends>QWidget</extends>
   <header>uitk.widgets.footer</header>
  </customwidget>"""


def build_footer_item(item_indent: str) -> str:
    """Build a Footer <item>...</item> block with the given indentation for <item>."""
    sp = item_indent
    s1 = sp + " "
    s2 = sp + "  "
    s3 = sp + "   "
    s4 = sp + "    "
    return (
        f"{sp}<item>\n"
        f"{s1}<widget class=\"Footer\" name=\"footer\" native=\"true\">\n"
        f"{s2}<property name=\"minimumSize\">\n"
        f"{s3}<size>\n"
        f"{s4}<width>0</width>\n"
        f"{s4}<height>20</height>\n"
        f"{s3}</size>\n"
        f"{s2}</property>\n"
        f"{s2}<property name=\"maximumSize\">\n"
        f"{s3}<size>\n"
        f"{s4}<width>16777215</width>\n"
        f"{s4}<height>20</height>\n"
        f"{s3}</size>\n"
        f"{s2}</property>\n"
        f"{s1}</widget>\n"
        f"{sp}</item>\n"
    )


def find_target_layout_close(text: str) -> int:
    """Return the absolute index of the `</layout>` tag that closes the layout
    holding the Header widget (or the outermost <layout> if no Header)."""
    header = re.search(r'<widget class="Header"', text)
    if header:
        # Walk forward from after the Header widget. Track layout depth starting
        # at 0; the first `</layout>` we see at depth 0 is Header's parent close.
        i = header.end()
        depth = 0
        for m in re.finditer(r"<layout\b|</layout>", text[i:]):
            tag = m.group(0)
            abs_pos = i + m.start()
            if tag.startswith("<layout"):
                depth += 1
            else:
                if depth == 0:
                    return abs_pos
                depth -= 1
        raise RuntimeError("No </layout> found after Header")

    # No Header (e.g. workspace_map.ui). Use the first <layout> directly under
    # the top-level <widget>. Walk forward from the first <layout> tracking
    # depth until depth returns to 0.
    first = re.search(r"<layout\b", text)
    if not first:
        raise RuntimeError("No <layout> in file")
    i = first.end()
    depth = 1
    for m in re.finditer(r"<layout\b|</layout>", text[i:]):
        tag = m.group(0)
        abs_pos = i + m.start()
        if tag.startswith("<layout"):
            depth += 1
        else:
            depth -= 1
            if depth == 0:
                return abs_pos
    raise RuntimeError("Unbalanced layouts")


def insert_footer_item(text: str) -> str:
    close_pos = find_target_layout_close(text)
    line_start = text.rfind("\n", 0, close_pos) + 1
    close_indent = text[line_start:close_pos]  # spaces before </layout>
    item_indent = close_indent + " "  # items are 1 space deeper
    block = build_footer_item(item_indent)
    # Insert before the </layout> line (text[:line_start] ends just after \n).
    # Then re-emit close_indent so the </layout> line keeps its original indent.
    return text[:line_start] + block + close_indent + text[close_pos:]


def add_footer_customwidget(text: str) -> str:
    if "<customwidgets>" in text:
        # Insert Footer as the first customwidget so it appears alongside Header.
        return text.replace(
            "<customwidgets>",
            "<customwidgets>\n" + FOOTER_CUSTOMWIDGET,
            1,
        )
    # No customwidgets section; create one before <resources/>.
    block = " <customwidgets>\n" + FOOTER_CUSTOMWIDGET + "\n </customwidgets>\n"
    if " <resources/>" in text:
        return text.replace(" <resources/>", block + " <resources/>", 1)
    if "<resources/>" in text:
        return text.replace("<resources/>", block + "<resources/>", 1)
    raise RuntimeError("Could not find insertion point for <customwidgets>")


def remove_existing_footer(text: str) -> str:
    """Strip any prior Footer item + customwidget so we can re-insert cleanly."""
    # Footer item: <item>\n ... <widget class="Footer" ... </widget>\n ... </item>\n
    text = re.sub(
        r"[ \t]*<item>\s*<widget class=\"Footer\"[\s\S]*?</widget>\s*</item>\n",
        "",
        text,
    )
    # Footer customwidget block.
    text = re.sub(
        r"[ \t]*<customwidget>\s*<class>Footer</class>[\s\S]*?</customwidget>\n",
        "",
        text,
    )
    return text


def process(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    had_footer = 'class="Footer"' in text
    if had_footer:
        text = remove_existing_footer(text)
    new_text = insert_footer_item(text)
    new_text = add_footer_customwidget(new_text)
    # Validate that result is well-formed XML.
    try:
        ET.fromstring(new_text)
    except ET.ParseError as e:
        raise RuntimeError(f"Generated XML invalid: {e}") from e
    path.write_text(new_text, encoding="utf-8")
    return "rewritten" if had_footer else "added"


def main():
    results = []
    for rel in UI_FILES:
        path = ROOT / rel
        if not path.exists():
            results.append((rel, "MISSING FILE"))
            continue
        try:
            status = process(path)
        except Exception as e:
            status = f"ERROR: {e}"
        results.append((rel, status))
    width = max(len(r[0]) for r in results)
    for rel, status in results:
        print(f"{rel.ljust(width)}  {status}")
    errs = [r for r in results if r[1].startswith("ERROR") or r[1] == "MISSING FILE"]
    if errs:
        sys.exit(1)


if __name__ == "__main__":
    main()
