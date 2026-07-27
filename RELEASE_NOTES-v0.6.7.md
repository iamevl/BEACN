# BEACN v0.6.7

## Static Assets Hotfix

Fixes the unstyled dashboard and inactive controls introduced by the frontend split.

### Root cause
The v0.6.6 Dockerfile copied `templates/` into the image but did not copy the new
`static/` directory. Flask therefore returned HTTP 404 for:

- `/static/css/app.css`
- `/static/js/app.js`

### Fixed
- Added `COPY static ./static` to the Dockerfile.
- Preserved the v0.6.6 frontend separation.
