# Changelog

All notable changes to Splat Workshop. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), versions follow [Semantic Versioning](https://semver.org/).

## [0.2.1] – 2026-10-14

### Added

- **Automated tests** (57) that run on every push via GitHub Actions – no GPU, recordings or Frame needed: the server's protection against other websites and malformed requests, scenes, groups, conversions with cancel/start over/resume, tours, the four corrections on synthetic rooms with a known error, borders through doors, and the floor plan's orientation. See *Development* in the README.

### Fixed

- After cancelling, a conversion counted as finished as soon as the sleep inhibitor ended, while `splat.sh` was still putting the previous scene back – now the whole job has to end.

## [0.2.0] – 2026-10-14

The big one: **put the rooms of your home together into one walkable tour.**

### Added

- **Groups**: put scenes together in a named group, e.g. all rooms of a flat. A group shows collapsed (preview, size, tour) or expanded with its scenes. Scenes recorded right after each other are suggested as a group.
- **Tours**: *Put the tour together* turns the rooms of one recording session into one scene – a **full version** for Gaussian XR on the PC and Spirula, and a **smaller version for the Frame** (2 / 3.5 / 5 M splats). Runs in the background like a conversion, with progress, cancel and notification; the floor plan of all rooms becomes the gallery preview.
- **Corrections to choose** when putting a tour together, so the rooms meet without visible steps: *A* as delivered by the pipeline, *B* every room fitted over all its images (default), *C* plus a joint shift over the overlaps, *D* plus shift and slight turn. Borders between recordings run through doors (walking in and out of a room is ignored) or by nearest camera. Afterwards the group shows how well the overlaps match, so variants can be compared.
- **Memory estimate** before putting a tour together, with the free memory; the full version (the step that needs the most memory) can be left out.
- **Cancelled runs** are listed with how far they got and their size: resume where the pipeline can continue, start over otherwise, or delete.
- **Delete scenes** (to the desktop trash, with confirmation) and **clean up** intermediate data or training images per scene, with the disk usage of every scene – or for all scenes of a group at once.
- **Rename recordings and scenes**; a renamed scene's folder, files and log follow, and its name plate in the gallery is redrawn.
- **Keeps the PC awake** while converting, copying or downloading (systemd sleep inhibitor).
- **Offline notice** when Splat Workshop stops responding; the page reconnects by itself.
- **Setup guide** for the Arcturus pipeline and Spirula Studio on Linux ([docs/arcturus-setup.md](docs/arcturus-setup.md)).

### Changed

- **Start over** keeps the finished scene until the new one is done and brings it back if the new run fails or is cancelled.
- **Resume** after an interrupted training restarts the training (images and camera positions are reused) instead of failing.
- The target defaults to *PC only* while the Frame is not reachable.
- Floor plans of several rooms are drawn from their group; the separate *Combined floor plan (beta)* button is gone.
- Scripts are read completely before they run, so updating Splat Workshop does not disturb a running conversion.

### Fixed

- Floor plans were drawn mirror-inverted.
- LAN mode now checks the client's address, not only the `Host` header; requests from other websites are refused; malformed requests get an error instead of a dropped connection.
- Long scene names no longer push the buttons out of the card; thumbnails of recordings with quotes in the name; contrast of grey text.

## [0.1.0] – 2026-10-12

First release.

- Local **web interface** and **tray icon** around the Arcturus Vision reconstruction pipeline and Spirula Studio.
- Recordings from the Frame (over SSH) or the PC, with thumbnails; **recording check** without AI (brightness, structure, fast pans, covered lenses – second by second).
- **Conversion** with quality presets or fine-tuned values, live progress, ETA and PSNR curve; conversions survive closing the browser or the server; cancel and resume.
- **Gaussian XR gallery**: preview image and name plate, copied into the `Scenes` folder on the Frame.
- **Floor plans** (experimental) in two styles with a measuring tool.
- Configurable video player; English and German interface.

[0.2.1]: https://github.com/thunderheronight/splat-workshop/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/thunderheronight/splat-workshop/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/thunderheronight/splat-workshop/releases/tag/v0.1.0
