---
name: Quality Graph
description: Minimal technical documentation for repository-owned quality infrastructure.
colors:
  ink: "#171717"
  paper: "#ffffff"
  link-blue: "#173ea5"
  code-surface: "#f3f3f3"
  divider: "#dddddd"
typography:
  display:
    fontFamily: "ui-monospace, SFMono-Regular, Cascadia Code, Roboto Mono, Consolas, monospace"
    fontSize: "2rem"
    fontWeight: 700
    lineHeight: 1.1
    letterSpacing: "-0.04em"
  body:
    fontFamily: "ui-monospace, SFMono-Regular, Cascadia Code, Roboto Mono, Consolas, monospace"
    fontSize: "0.875rem"
    fontWeight: 400
    lineHeight: 1.8
  navigation:
    fontFamily: "ui-monospace, SFMono-Regular, Cascadia Code, Roboto Mono, Consolas, monospace"
    fontSize: "0.8125rem"
    fontWeight: 400
    lineHeight: 1.5
  code:
    fontFamily: "ui-monospace, SFMono-Regular, Cascadia Code, Roboto Mono, Consolas, monospace"
    fontSize: "0.8125rem"
    fontWeight: 400
    lineHeight: 1.65
rounded:
  code: "3px"
spacing:
  xs: "6px"
  sm: "14px"
  md: "28px"
  lg: "40px"
  xl: "76px"
components:
  text-link:
    textColor: "{colors.link-blue}"
    typography: "{typography.navigation}"
  code-block:
    backgroundColor: "{colors.code-surface}"
    textColor: "{colors.ink}"
    typography: "{typography.code}"
    rounded: "{rounded.code}"
    padding: "19px 21px"
  docs-sidebar:
    textColor: "{colors.link-blue}"
    typography: "{typography.navigation}"
    width: "180px"
---

# Design System: Quality Graph

## Overview

**Creative North Star: "The Technical Pamphlet"**

Quality Graph uses the directness of a small, carefully typeset technical
publication. The interface recedes so identity, explanation, and documentation
can be understood without learning a visual language first. Whitespace supplies
structure; typography and ordinary links supply hierarchy.

The system is intentionally plain, not unfinished. It rejects promotional hero
layouts, decorative cards, gradients, ornamental containers, and application-like
chrome. The approved logo is the only illustration. One monospace family connects
product identity, prose, navigation, and code.

**Key Characteristics:**

- White canvas with near-black text and conventional blue links.
- One monospace type family across every surface.
- Centered, narrow reading measure independent of documentation navigation.
- Flat surfaces without shadows or decorative borders.
- Large areas of whitespace and very few interface elements.
- The eye logo appears prominently on the landing page, not as page furniture.

## Colors

The palette is the browser-native web reduced to five deliberate roles.

- **Link Blue** (#173ea5): navigation and text links; the only chromatic accent.
- **Ink** (#171717): primary text, headings, and active navigation.
- **Paper** (#ffffff): the universal page background.
- **Code Surface** (#f3f3f3): quiet separation for code samples.
- **Divider** (#dddddd): mobile navigation separation when space is insufficient.

**The Links Own Color Rule.** Blue is reserved for links. Do not use it for
headings, decoration, borders, or passive emphasis.

**The White Canvas Rule.** Pages use pure white. Do not introduce warm paper
tones, tinted sections, or alternating background bands.

## Typography

**Display Font:** system monospace stack: ui-monospace, SFMono-Regular, Cascadia
Code, Roboto Mono, Consolas, monospace.

**Body Font:** the same system monospace stack.

**Character:** One monospace voice makes the interface feel authored by the same
technical system it documents. Hierarchy comes from weight, size, and space.

- **Display** (700, 2rem, 1.1): documentation page titles.
- **Title** (700, 1rem, 1.4): section headings and navigation group labels.
- **Body** (400, 0.875rem, 1.8): prose in an article no wider than 680px.
- **Navigation** (400, 0.8125rem, 1.5): sidebar and simple page links.
- **Code** (400, 0.8125rem, 1.65): fenced code samples.

**The One Typeface Rule.** Every text role uses the same monospace stack.

**The Space Makes Hierarchy Rule.** Prefer spacing and weight over badges,
eyebrow text, uppercase, tracking, or decorative numbering.

## Layout

The landing page is a single centered column with a prominent logo lockup, one
short explanation, and one documentation link. It has no header, footer, hero
treatment, feature grid, or persistent navigation.

Documentation uses two independent axes. The article is exactly centered in the
viewport and capped at 680px. Section navigation is fixed 28px from the left edge,
180px wide, and vertically centered; its width never shifts the article.

At 800px and below, the sidebar returns to document flow above the article,
becomes a compact wrapping list, and uses one divider before the content.

## Elevation & Depth

The system is completely flat. It uses no shadows, gradients, glass, overlays, or
simulated depth. Whitespace and the code-surface tone are the only layering.

**The Flat Means Flat Rule.** Do not add shadows to navigation, code blocks,
links, the logo, or content containers.

## Shapes

Most elements have no container. Code blocks use a nearly square 3px radius.
Links remain underlined text. The eye logo keeps its authored organic outline.

## Components

### Brand Lockup

The original eye SVG sits beside the lowercase quality—graph wordmark. On the
landing page it is centered, prominent, and approximately 112px square. It is not
repeated in documentation because documentation has no header.

### Text Links

Links use Link Blue and native underlines offset by 2px to 3px. Focus-visible uses
a 2px Link Blue outline. Active navigation uses Ink, weight 700, and no underline.

### Code Blocks

Code blocks use Code Surface, Ink, 19px 21px padding, and a 3px radius. Long code
scrolls horizontally rather than shrinking or wrapping unpredictably.

### Documentation Sidebar

On desktop it is fixed 28px from the left edge, vertically centered, and 180px
wide. On mobile it becomes a wrapping list above the centered article.

## Do's and Don'ts

### Do:

- **Do** begin the landing page with the centered logo lockup.
- **Do** keep the article centered relative to the full viewport.
- **Do** use ordinary text links for navigation.
- **Do** preserve generous whitespace around short groups of content.
- **Do** keep code and sidebar text at least 0.8125rem.
- **Do** use the approved SVG without redrawing or approximating it.

### Don't:

- **Don't** add a top header to the landing page or documentation.
- **Don't** add cards, feature grids, decorative statistics, or promotional sections.
- **Don't** use beige, gradients, shadows, glass, or ornamental backgrounds.
- **Don't** let the sidebar push the article away from the viewport center.
- **Don't** use the logo as a tiny navigation icon.
- **Don't** introduce a second typeface.
