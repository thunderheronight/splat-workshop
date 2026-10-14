# Changelog

All notable changes to Splat Workshop. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), versions follow [Semantic Versioning](https://semver.org/).

## [0.2.3] – 2026-10-14

### Added

- **Scale for models**, e.g. a model railway: set Z, N, TT, H0, S, 0, G or your own scale for a scene or a whole group (optionally passed on to its scenes). Floor plan, room sizes, the scale bar and the measuring tool then show the size of the original – a 10 cm H0 model reads as 8.7 m. The floor plan also looks for walls and floors in the model's scale, so a model is drawn like a real building.
- **Walk through a model in its original size** in Gaussian XR (option): Splat Workshop sets Gaussian XR's `worldScale` when copying a scene or tour to the Frame – the scene files on the PC stay unchanged. *Not tested on the Frame yet.*

- **Versions** of a scene or tour, several side by side: fewer splats for the Frame (0.75 / 1 / 1.5 / 2 M), all or a limit for the PC (3 / 5 / 8 M), or an own size – each also in the original's size for models. Every version gets its own place in the Frame's gallery (e.g. "Flat · 1 M" next to "Flat · 2 M"), so you can find out yourself what runs smoothly. Frame rates measured on a Steam Frame are shown for information.
- **Fetch several recordings from the Frame at once**: *Fetch all* (everything not on this PC yet) or tick recordings and *Fetch selection*. They come one after the other, with one progress bar for all and *Cancel*; recordings ticked while fetching join the queue. A notification at the end says when the Frame can be switched off; one that fails does not stop the others.
- **"On the Frame" tab** under the finished scenes: everything in Gaussian XR's gallery on the Frame with its preview image – scenes, tours, versions and Gaussian XR's own examples – and *Off the Frame* to delete any of them there (the PC is not touched).

### Changed

- **Scenes go to Gaussian XR's folder for personal scenes** (`Documents/GaussianXR` on the Frame) instead of the app's own `Scenes` folder, which a Steam update or "verify files" can reset. They appear in the gallery after Gaussian XR's examples. Scenes copied by older versions move over when copied again, or all at once with *Move to own scenes* in the *On the Frame* tab; numbers of new folders count both places.
- **No backup copies on the Frame any more**: everything is on the PC, and *Copy again* restores a scene. Copies left in `Documents/GaussianSplats` by older versions are removed together with their scene.
- **1 M splats is the recommended size for the Frame** (preselected for versions and tours, also the default of `combine.sh`, was 3.5 M). Measured on a Steam Frame with one tour: 0.75 M 36 fps, 1 M 35 fps, 1.5 M 29 fps, 2 M 24 fps, 5 M 14 fps – 1 M looked clearly sharper than 0.75 M, 1.5 M a little sharper but already slightly unsteady. All sizes stay selectable.
- Putting a tour together offers 0.75 to 2 M splats or an own size for its Frame version (was 2 / 3.5 / 5 M).

### Fixed

- The floor plan no longer fails for scenes in which it finds no walls (e.g. a model without its scale).

## [0.2.2] – 2026-10-14

### Changed

- **Tests hardened after an independent review in two rounds** (213 instead of 57): every deliberately broken piece of code from the review is now caught (mutation testing). New: the recording check, renaming recordings, the Frame's list, downloads (also a broken one) and copying scenes to the Frame (through a fake `ssh`), recovery after a power cut, what happens after a job (notification, tidying up), *Start over* from the list of cancelled runs, scenes being left alone while they are computed, checks before a conversion starts (fake `ffprobe` instead of switching the check off), splat orientation and size when putting rooms together, correction D against C on a turned tracking, the reduced Frame version. Synthetic rooms now have tracking noise, scale errors and turned splats. Even with broken code the tests can no longer open anything on the desktop or end the test run unnoticed.
- Known limit, now written down as a test: walking into a room is only recognised up to 0.4 m from another recording's path.

### Fixed

- Wrongly typed fields (e.g. `members: 5` when saving a group) were answered with an internal error or stored – now they get a clean 400.

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

[0.2.3]: https://github.com/thunderheronight/splat-workshop/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/thunderheronight/splat-workshop/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/thunderheronight/splat-workshop/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/thunderheronight/splat-workshop/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/thunderheronight/splat-workshop/releases/tag/v0.1.0
