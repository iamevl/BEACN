# Network Dashboard v0.6.6

## Frontend Foundation

This release begins the frontend refactor without changing dashboard behaviour.

### Changed
- Extracted all embedded CSS from `templates/index.html` into `static/css/app.css`.
- Extracted all embedded JavaScript into `static/js/app.js`.
- Reduced `index.html` to the page structure and Flask template values.
- Added version-based cache busting to CSS and JavaScript asset URLs.
- Preserved all v0.6.5 functionality, including hardware history tooltips and timestamps.

### Why
The original template had grown to roughly 1,460 lines. Separating structure, styling,
and behaviour gives Docker Monitoring and future features a clean foundation.
