# Rover Design System

Reference guide for building Rover's web dashboard. All values are extracted from the production landing page.

---

## Brand Voice & Copy

**Tone:** Playful, clever, grounded. Rover is a friendly robot character (Wall-E / Duo the Owl vibes) who works quietly in the background.

**Do:**
- Speak in short, direct sentences ("Rover finds price drops and gets your money back.")
- Personify Rover as a helpful companion ("Your refund robot", "A tiny robot with a big job")
- Use action verbs: finds, catches, handles, contacts, saves
- Frame benefits as discovery, not loss ("hidden savings" angle)
- Be conversational and trustworthy ("Rover's helpful, not creepy.")
- Keep CTAs clear and benefit-driven ("Get Started", "Pay nothing if Rover finds nothing")

**Don't:**
- Use corporate jargon, hype words ("revolutionary"), or salesy language
- Force puns or be overly cute
- Use fear/loss aversion framing

**Audience:** Savvy online shoppers who get it quickly.

---

## Colors

### Core Palette

| Name | Hex | Usage |
|------|-----|-------|
| Accent Red | `#F55446` | CTAs, highlights, interactive elements. Use sparingly. |
| Accent Red Hover | `#E04438` | Hover/pressed states for accent elements |
| Accent Red Light | `#ff8a7e` | Top of 3D button gradients |
| Carbon | `#1A1D1E` | Headings, primary text, dark backgrounds |
| Cream | `#FFFFFF` | Primary backgrounds |
| Cream Dark | `#FAFAFA` | Secondary/inset backgrounds |

### Semantic Colors

| Name | Value | Usage |
|------|-------|-------|
| Success | `#10b981` (emerald-500) | Checkmarks, positive states |
| Success Dark | `#059669` (emerald-600) | Status indicators |
| Success BG | emerald-50 | Light success backgrounds |
| Warning | `#f59e0b` (amber-500) | Time/alert icons |
| Warning BG | amber-50 | Light warning backgrounds |

### Text Colors

| Token | Value | Usage |
|-------|-------|-------|
| Primary | `text-carbon` | Headings, body text |
| Secondary | `text-carbon/50` | Descriptions, secondary info |
| Tertiary | `text-carbon/35` | Hints, timestamps |
| Muted | `text-carbon/25` | Disabled, decorative |
| On Dark | `text-white` | Text on dark backgrounds |
| On Dark Secondary | `text-white/50` | Secondary text on dark |

### Opacity Scale (commonly used)

`/5`, `/10`, `/15`, `/20`, `/25`, `/30`, `/35`, `/50`, `/60`, `/70`

---

## Typography

### Font Stack

- **Headings:** Space Grotesk (geometric, sharp)
- **Body:** DM Sans (clean, neutral)

### Scale

| Element | Size | Weight | Line Height | Letter Spacing |
|---------|------|--------|-------------|----------------|
| Display / Hero H1 | `clamp(2.2rem, 4.5vw, 3.5rem)` | Extrabold | `1.1` | `-0.03em` |
| Section Heading | `clamp(2rem, 4vw, 3rem)` | Extrabold | `1.1` | `-0.025em` |
| Card Heading | ~20-24px | Bold | default | `-0.01em` |
| Body Large | `17px` (desktop) / `15px` (mobile) | Medium | `relaxed` | default |
| Body | `15px` | Medium | `relaxed` | default |
| Small / UI | `14px` | Medium/Semibold | default | default |
| Caption | `13px` | Medium | default | default |
| Micro | `11px` | Semibold | default | default |
| Nano | `9px` | Semibold | default | default |
| Uppercase Label | `11-13px` | Semibold | default | `0.15em` |
| Step Label | `11-13px` | Semibold | default | `0.1em` |

### Font Weights

- `extrabold` — Display headings
- `bold` — Section headings, emphasis
- `semibold` — Labels, buttons, small text
- `medium` — Body text, UI elements

---

## Spacing

### Section Padding

| Context | Mobile | Desktop |
|---------|--------|---------|
| Section vertical | `py-28` | `py-36` |
| Container horizontal | `px-4` | `px-8` / `px-16` / `px-20` |
| Hero top | `pt-28` | `pt-28` |
| Card internal | `p-5` | `p-8` to `p-12` |

### Vertical Rhythm

| Between | Gap |
|---------|-----|
| Heading to body | `mb-4` (small), `mb-6` (medium), `mb-10` (large) |
| Body to CTA | `mb-10` |
| Card items | `gap-6` (mobile), `gap-8` (desktop) |
| Icon + text | `gap-2.5` to `gap-3` |
| List items | `space-y-1.5` |

### Common Gap Values

`gap-1`, `gap-2`, `gap-3`, `gap-4`, `gap-5`, `gap-6`, `gap-8`, `gap-12`, `gap-16`

---

## Border Radius

| Element | Radius |
|---------|--------|
| Buttons, inputs, pills, badges | `rounded-full` |
| Cards, major containers | `rounded-2xl` |
| Medium cards, secondary UI | `rounded-xl` |
| Chat bubbles, small cards | `rounded-xl` |
| Icon badges | `rounded-xl` or `rounded-full` |

---

## Shadows

### Card Shadows

**Elevated (`.card-elevated`):**
```
rest:  0 1px 3px rgba(0,0,0,0.02), 0 4px 12px rgba(0,0,0,0.02)
hover: 0 2px 4px rgba(0,0,0,0.03), 0 8px 20px rgba(0,0,0,0.04)
       + translateY(-2px)
```

**Floating elements:**
```
small:  0 2px 12px rgba(0,0,0,0.08)
medium: 0 4px 24px rgba(0,0,0,0.1)
large:  0 4px 24px rgba(0,0,0,0.12), 0 16px 56px rgba(0,0,0,0.16)
accent: 0 2px 12px rgba(245,84,70,0.15)
```

**Input focus ring:**
```
0 0 0 3px rgba(245,84,70,0.15)
```

**Input inset:**
```
inset 0 2px 4px rgba(0,0,0,0.04)
```

---

## Buttons

### Primary CTA (`.btn-3d`)

```css
background: linear-gradient(180deg, #ff8a7e 0%, #F55446 50%, #e04438 100%);
border-radius: 9999px;
padding: px-7 py-3.5;
color: white;
font-size: 15px;
font-weight: semibold;
box-shadow:
  0 4px 12px rgba(245,84,70,0.3),
  0 12px 28px rgba(245,84,70,0.2),
  inset 0 -1.5px 3px rgba(255,255,255,0.35);
transition: all 0.2s cubic-bezier(0.22, 1, 0.36, 1);

/* Hover: lift up */
hover: translateY(-2px), enhanced shadows

/* Active: press down */
active: translateY(1px), reduced shadows
```

### Dark CTA (`.btn-3d-dark`)

```css
background: linear-gradient(180deg, #363b3e 0%, #1A1D1E 50%, #101213 100%);
border-radius: 9999px;
padding: px-7 py-3.5;
color: cream;
font-size: 15px;
font-weight: semibold;
box-shadow:
  0 4px 12px rgba(0,0,0,0.2),
  0 12px 28px rgba(0,0,0,0.15),
  inset 0 -1.5px 3px rgba(255,255,255,0.1);
```

### Secondary / Nav Button

```css
padding: px-4 py-2;
font-size: 14px;
font-weight: medium;
border-radius: 9999px;
color: text-carbon/70 -> hover text-carbon;
background: transparent -> hover bg-black/[0.04];
transition: colors 300ms;
```

---

## Borders

| Context | Style |
|---------|-------|
| Card border | `border border-black/[0.06]` |
| Card border (stronger) | `border border-black/[0.08]` |
| Dashed placeholder | `border-2 border-dashed border-black/[0.08]` |
| Accent highlight | `border border-accent/20` |
| Dark mode input | `border border-white/[0.08]` |
| Divider line | `h-px bg-black/[0.06]` |

---

## Glassmorphism

**Navbar (`.nav-glass`):**
```css
background: rgba(255,255,255,0.4);
backdrop-filter: blur(24px) saturate(180%);
border: 1px solid rgba(255,255,255,0.6);
```

**Navbar scrolled:**
```css
background: rgba(255,255,255,0.5);
border: 1px solid rgba(255,255,255,0.7);
box-shadow: 0 2px 0 rgba(0,0,0,0.02), 0 4px 16px rgba(0,0,0,0.04), 0 12px 40px rgba(0,0,0,0.06);
```

**Mobile menu:**
```css
background: rgba(255,255,255,0.5);
backdrop-filter: blur(12px) saturate(180%);
border: 1px solid rgba(255,255,255,0.6);
```

---

## Cards

### Elevated Card

```
border: border-black/[0.06]
radius: rounded-2xl
shadow: card-elevated (see shadows)
padding: p-5 to p-10
hover: lift -2px + stronger shadow
transition: all 0.5s cubic-bezier(0.22, 1, 0.36, 1)
```

### Inset Card

```
background: #FAFAFA (cream-dark)
border: 1px solid rgba(0,0,0,0.06)
radius: rounded-2xl
padding: p-8 to p-10
hover: background shifts to #F5F5F5
transition: all 0.4s cubic-bezier(0.22, 1, 0.36, 1)
```

---

## Animations & Transitions

### Easing

- **Primary (snappy):** `cubic-bezier(0.22, 1, 0.36, 1)` — buttons, cards, entrances
- **Ease out:** general exits
- **Ease in-out:** looping animations

### Durations

| Speed | Duration | Usage |
|-------|----------|-------|
| Fast | `200ms` | Buttons, inputs, toggles |
| Medium | `300ms` | Nav transitions, menus |
| Standard | `500ms` | Card hover elevations |
| Slow | `700-800ms` | Page entrance animations |

### Entrance Animation Pattern (Framer Motion)

```js
initial: { opacity: 0, y: 20, filter: "blur(10px)" }
animate: { opacity: 1, y: 0, filter: "blur(0px)" }
transition: { duration: 0.7, ease: [0.22, 1, 0.36, 1] }
// Stagger children by 0.1-0.2s increments
```

### Floating / Idle Animation

```js
animate: { y: [0, -8, 0] }
transition: { duration: 3, repeat: Infinity, ease: "easeInOut" }
```

---

## Layout

### Max Widths

| Context | Value |
|---------|-------|
| Page container | `max-w-7xl` (80rem) |
| Wide sections | `max-w-[1400px]` |
| Form inputs | `max-w-md` (28rem) |
| Text blocks | `max-w-lg` (32rem) |

### Grid Patterns

- 2-column: `grid md:grid-cols-2 gap-12 md:gap-16`
- 3-column: `grid md:grid-cols-3 gap-6 md:gap-8`
- Responsive cards: `grid sm:grid-cols-2 gap-6 md:gap-8`

### Centering

- `mx-auto` for horizontal centering
- `flex items-center justify-center` for flex centering

---

## Responsive Breakpoints

| Breakpoint | Prefix | Key Changes |
|------------|--------|-------------|
| Mobile | (none) | Single column, `px-4`, stacked layouts |
| Tablet | `md:` | 2-3 column grids, `px-8`, inline layouts |
| Desktop | `lg:` | Max padding `px-20`, full layouts |

**Common patterns:**
- `flex-col sm:flex-row` (stack on mobile, row on tablet)
- `hidden md:flex` (show on tablet+)
- `text-[15px] md:text-[17px]` (larger body on desktop)

---

## Icons

- Inline SVGs only (no image files)
- Sizes: `16-18px` (in buttons), `24px` (features), `28px` (section headers)
- Stroke-based: `strokeWidth="1.5"` or `1.8`, `strokeLinecap="round"`, `strokeLinejoin="round"`
- Color: `stroke="currentColor"` (inherits from parent text color)

---

## Z-Index Stack

| Layer | Value |
|-------|-------|
| Background decorations | `z-[1]` |
| Card overlays | `z-10` |
| Dropdowns | `z-[15]` |
| Fixed nav | `z-50` |
| Modals / menus | `z-[100]` |

---

## Misc

**Text selection:**
```css
::selection { background: #F5544633; color: #1A1D1E; }
```

**Font smoothing:**
```css
-webkit-font-smoothing: antialiased;
-moz-osx-font-smoothing: grayscale;
```

**Grain texture overlay:** Applied via `.grain::before` pseudo-element with SVG noise filter at `opacity: 0.025`.
