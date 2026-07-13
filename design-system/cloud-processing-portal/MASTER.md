# Cloud Processing Portal Design System

## Product contract

The portal has exactly three levels:

1. Choose a department.
2. Choose one processing workflow from that department.
3. Upload files, run the Python workflow, and download the result.

Department and workflow counts are configuration-driven. Do not add explanatory dashboards, architecture panels, marketing sections, or visible API details.

## Direction

- Apple-inspired restraint for a web utility, not an imitation iPhone screen.
- Light gray page, white interactive surfaces, system typography.
- One primary blue action per state; green only confirms success; red only reports errors.
- Generous whitespace and a single-column choice list.
- Progressive disclosure: access-token settings stay collapsed unless needed.

## Tokens

- Page: `#F5F5F7`
- Surface: `#FFFFFF`
- Primary text: `#1D1D1F`
- Secondary text: `#6E6E73`
- Primary action: `#0071E3`
- Success: `#248A3D`
- Error: `#D70015`
- Radius: 14 / 20 / 28px
- Spacing rhythm: 4 / 8 / 12 / 16 / 20 / 28 / 40 / 52 / 72px
- Font: `-apple-system`, `BlinkMacSystemFont`, `PingFang SC`, sans-serif

## Interaction rules

- Every choice row is at least 82px tall and the entire row is clickable.
- Touch targets are at least 44px.
- Motion is limited to 160-180ms hover and press feedback.
- Respect `prefers-reduced-motion`.
- File selection, upload, processing, success, failure, and download availability must each have explicit text feedback.
- Never claim upload success merely because a local file was selected. Use “文件已添加” until the server accepts the request.

## Responsive rules

- One-column layout at every width.
- Maximum content width: 760px for lists, 680px for the runner.
- Validate at 375px, 768px, 1024px, and 1440px.
- No horizontal scrolling and no hover-only interactions.
