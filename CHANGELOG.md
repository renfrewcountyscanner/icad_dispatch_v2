# Changelog

All notable changes to iCAD Dispatch will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [2.1.0] - 2026-04-17

### Added
- **Stats Summary Card** — Shows total calls, triggered count, transcribed count, average duration, and incident type breakdown when a system is selected
- **Date Range Filter** — New "From" and "To" date pickers to filter tone hits by date
- **Incident Category Filter** — New "Incident" dropdown to filter by Fire, Medical, Traffic, Rescue, HazMat, Utilities, Other
- **Trigger Filter Dropdown** — New dropdown to filter by specific trigger (department/station)
- **System Column** — New column in tone hits table showing which radio system each call belongs to
- **"All Systems" Default View** — Tone Hits page now loads all systems by default when no system is selected
- **Version Badge** — Version number displayed in the top navigation bar next to the logo

### Changed
- **Column Reordering** — Talkgroup, Duration, and Tones columns moved to the left (compact numeric columns); Triggered column now has more horizontal space
- **Column Widths** — Fixed widths applied to narrow columns: Talkgroup (4.5rem), Duration (5rem), Tones (3.5rem), System (9rem)
- **Responsive Priorities** — Triggered column now stays visible longer on smaller screens

### Fixed
- Missing opening quote on navbar brand `<a>` tag (HTML bug)
- Logged-in users seeing blank page at root URL — now redirects to /dashboard
- Wrong PWA app title "iCAD NWS Alerts" changed to "iCAD Dispatch"
- Duplicate `tab-content` div/ID in Radio Systems settings page (caused tab switching issues)
- Upload tab using same icon as Tones tab — now uses `bi-cloud-upload`
- Notifier override labels (discord/make/telegram) now properly cased
- No developer warning on Debug page — now shows warning banner
- No unsaved changes warning when switching tabs on system settings — now warns before leaving with unsaved changes
- No visual feedback when auto-refresh is active — now shows green "Live" badge

---

## [1.0.1] - 2025-12-?? (First tagged release)

### Added
- Initial release
- Tone detection and trigger system
- Radio system management
- Call recording and transcription
- Alert notifications (Discord, Telegram, Make, n8n)
- Dashboard with tone hits visualization
- Debug page for developers