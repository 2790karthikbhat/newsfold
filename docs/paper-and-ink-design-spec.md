# Paper & Ink — Reading App Design Spec
*Working title. App name TBD.*

## Design Philosophy

The app should feel like reading a well-kept paperback by lamplight, not like operating a productivity dashboard. Every metaphor draws from physical reading objects — paper, ink, ribbon bookmarks, dog-eared corners, margin notes — rather than glossy app-store visual language (no glassmorphism, no gradient-glow cards, no neon accents).

The reading queue is a stack of physical items on a desk, not a database table. Progress is a ribbon, not a progress bar. Highlights are marginalia, not a notes list.

Two explicit non-goals:
- Not another "premium dark mode + gold accent" app — that's the current default AI-app look and reads as generic.
- Not a stats dashboard. Reading numbers exist, but stay quiet and secondary to the reading itself.

## Color System

**Light mode ("Daylight")**
- Background: `#F5F0E6` — warm aged paper, not stark white
- Primary text (ink): `#211D18` — warm near-black, never pure black
- Ribbon accent: `#A8402E` — muted terracotta/brick red, like a cloth bookmark ribbon or wax seal
- Secondary accent (tags/metadata): `#6B6250` — soft olive-taupe
- Card/surface: `#EDE6D6` — slightly deeper paper tone for layering, no shadows, use tone shift instead of elevation

**Dark mode ("Candlelight")** — not OLED black
- Background: `#2A241D` — espresso brown, warm dark, never `#000000`
- Primary text: `#EFE7D6` — warm cream ink
- Ribbon accent: `#C96A4F` — warmed terracotta, brighter for contrast
- Secondary accent: `#9C8F73`
- Card/surface: `#332C22`

No pure black, no pure white anywhere in the palette. Warmth is the constant.

## Typography

- **Headers & body reading text**: New York (serif), Regular/Semibold. Same family used for Fincom's "Fading Ledger," giving the two apps a shared type voice without being visually identical.
- **UI chrome (buttons, labels, metadata)**: San Francisco, small caps or letter-spaced uppercase for labels ("CONTINUE READING", "3 MIN READ") — this is where the two apps diverge, since Fincom uses digit-heavy sans labels and this app is text-heavy.
- **Reader type controls**: user-adjustable size (Aa), plus a serif/sans toggle for accessibility, matching the reference screenshot's "Aa" affordance.
- Line height for reading body: 1.5–1.6, measure capped around 62–68 characters per line regardless of device width (center-gutter margins on larger screens).

## Core Metaphors

1. **Ribbon bookmark** — replaces the progress bar entirely. A vertical ribbon rendered along the right edge of a card, its visible length proportional to % read. On the reader screen itself, a thin ribbon tab sits at the top, draggable to jump position (visual, not just decorative).
2. **Dog-eared corner** — top-right corner of any saved-item card folds down slightly (subtle, not skeuomorphic-heavy) to indicate "has unread progress." Tapping the fold is a shortcut to resume.
3. **Inbox tray** — new items from Share Sheet / email / RSS land in a distinct "Inbox" stack, visually a slightly messier/looser stack than the organized "Library," reinforcing the idea of an unsorted in-tray you clear out.
4. **Marginalia** — highlights and notes are shown as torn-paper snippet cards, source citation in small caps beneath, no list-row chrome.

## Screens

### 1. Library (Home)
Replaces the stats-dashboard-style home in the reference mockup. Structure, top to bottom:
- Small ledger line at top: `247p · 4 books · 6h46m today` — one line, quiet, no big stat cards
- **Continue Reading**: horizontal stack of 2–3 cards, each with dog-eared corner + ribbon showing progress
- **Inbox**: newly saved/unsorted items (from share sheet, email, RSS), looser visual stack
- **Library**: sorted/read items, simple list with ribbon indicators

### 2. Reader
- Full-bleed paper background, no chrome except: close (X), Aa (type settings), share, ribbon tab at top for position
- Single column, generous margins, no card/border around the text itself — it should feel like you're looking at a page, not a UI panel
- Tap-and-hold to highlight; highlight color is the ribbon terracotta at low opacity, not a bright yellow

### 3. Highlights & Notes
- Grid or single column of marginalia cards (torn-edge visual treatment, subtle — a jagged top edge via a thin SVG mask, not a heavy graphic)
- Each shows: excerpt, source title + domain in small caps, page/location
- Filter/sort controls styled as simple text toggles, not pill buttons

### 4. Inbox / Add (new — not in original reference)
- Where Share Sheet, email-forward, and RSS items land before being read
- Swipe actions: Keep (moves to Library), Archive, Discard
- This is the screen that makes the multi-source ingestion pipeline visible and usable, since three input paths converging silently would be confusing without a triage step

## Motion & Interaction Notes
- Page turns: subtle horizontal slide, not a literal 3D page-curl (too skeuomorphic/dated)
- Ribbon animates its length change smoothly as reading position updates
- No spring-bounce on every interaction — keep motion calm and short-duration (150–200ms), matching the "calm UX" goal

## Theme Token System (v1 scope)

No marketplace in v1. The app is built on a token-based theme system from the start so this can grow later without a rewrite — but v1 ships as curated + exportable only, no submission/discovery/store.

**Token schema** (drives every color/type reference in the app — nothing hardcoded):
```json
{
  "name": "Paper & Ink",
  "colors": {
    "background": "#F5F0E6",
    "surface": "#EDE6D6",
    "textPrimary": "#211D18",
    "accent": "#A8402E",
    "accentSecondary": "#6B6250"
  },
  "typography": {
    "readingFont": "New York",
    "uiFont": "SF Pro",
    "bodyLineHeight": 1.55
  }
}
```

**Built-in curated themes for v1**: Paper & Ink (default), Candlelight (dark variant), and one additional variant TBD — kept to 3–4 so the app stays coherent rather than sprawling into a skin gallery.

**Export/import**: users can export the active theme (including any tweaks to accent color, etc.) as a `.json` file via the Share Sheet, and import a theme file the same way. No backend, no store — sharing happens person-to-person (AirDrop, Files, links) the way VS Code/Zsh themes spread before marketplaces existed. If this gets organic traction, a real community store becomes a v2+ consideration, not a v1 commitment.

## Open Decisions for Next Pass
- App name/icon (icon should probably be a literal ribbon-bookmark mark, simple, one or two colors)
- Whether Inbox triage is required before reading, or items are readable directly from Inbox too
- Empty states (empty Inbox, empty Library) — should carry the same paper/ink warmth, not generic illustrations
