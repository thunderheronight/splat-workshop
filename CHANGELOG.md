# Changelog

All notable changes to Splat Workshop. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased] – 0.5

### Added

- **Mesh export**: *Mesh …* on every scene and tour turns the splats into a surface with Spirula Studio (`spirula mesh`) – for Blender and other 3D programs, or for 3D printing. Formats: **GLB** (recommended: colours or texture in one file), **STL** (no colours, for slicers), **OBJ** (with a texture if possible, otherwise without colours) and **PLY** (colours per corner).
  - **Stands upright, floor at height 0**: our scenes have Y pointing down like the Steam Frame's tracking, so a mesh used to lie on its side in Blender. Now PLY and STL get Z up, GLB and OBJ Y up as their standard and custom want (Blender turns those into Z up itself on import – checked in Blender 5.2 for every format); the floor – found as for the floor plan – is moved to height 0. In metres.
  - What lies well outside the room is left out before meshing (as the *Edges* clean-up does) – otherwise big blobs beyond the walls become lumps floating around the room.
  - A **texture** from the training images when they are still there; after *clean up → training images*, colours per corner from the splats.
  - The mesh lives in the scene's folder (`mesh/`); a new one replaces the old one. *Mesh ✓* shows which scenes have one.
  - Also on the command line: `scripts/mesh.sh` and `scripts/upright.py`.

### Fixed

- A versions job with one version said "1 versions".

## [0.4.2] – 2026-10-15

### Fixed

- **Unfolding recordings or versions sometimes did nothing**: every click loaded the whole scene list again, which took about 3 seconds while the Frame was switched off – clicking again in that time folded it back. Now it opens and closes at once, and an expired listing of the Frame is renewed in the background instead of holding up the scene list.
- **"To the Frame" on a version** could be clicked while the Frame was not reachable; now it is greyed out like the one on the scene.

## [0.4.1] – 2026-10-15

Setup made clearer – thanks to the first two issues from outside (#1, #2).

### Fixed

- **A typo in the config is pointed out**: a setting Splat Workshop does not know – such as `splat-transform =` instead of `splat_transform =` – used to be ignored silently, so splat-transform stayed "not found" without a reason. Now the setup hints name it, with the right spelling. `install.sh` does too.
- **splat-transform on the shell's PATH only**: `install.sh` checked the PATH of the shell it runs in, but Splat Workshop, started from the desktop or the tray, does not read `.bashrc`/`.zshrc` – the installer said ✓, the app said "not found". Now `install.sh` writes the full path into a new config, or prints the line to add to an existing one; the hint in the app explains why and how.
- **The npm instructions** in the setup guide installed into a folder whose path did not match the one given for the config. Now: `npm install -g --prefix ~/arcturus/node @playcanvas/splat-transform` (no root needed) and `~/arcturus/node/bin/splat-transform`.
- **The Arcturus pipeline and PyAV 19** ("unexpected keyword argument 'metadata_errors'"): Splat Workshop now spots a pipeline that is not pinned to PyAV below 19 before you start, and names the cause and the fix when a conversion fails because of it – instead of only a traceback. It does not change the pipeline itself.

## [0.4.0] – 2026-10-15

### Added

- **Clean up a version** (experimental): in the versions dialog, four sliders from *off* to 100 % – the scene itself stays as it is, only the new version is cleaned up, so it can be compared side by side with one without (e.g. *all splats* for the PC).
  - **Ceiling**: a white ceiling gives the reconstruction nothing to hold on to – it stays thin, with a dark veil under it. Takes out the veil (deeper with more strength; dark things within 50 cm of a wall, like a cupboard top or a bird cage, stay) and what lies above the ceiling, and puts in a closed ceiling in the colour of what there is of it. The ceiling's height is found where walls and ceiling meet. In a real living room: the veil of about 210,000 splats gone, the ceiling closed with 72,000.
  - **Floor**: fills holes in the floor in the colour of the floor around them, and takes out what lies under it.
  - **Haze**: takes out big, nearly transparent blobs floating in the room – measured by their *smallest* axis, so long thin things (the bars of a bird cage, cables) are no haze and stay.
  - **Edges**: takes out what lies well outside the room – beyond the walls, above the ceiling, under the floor.
  - Also on the command line: `scripts/clean.py`.
- **The versions dialog, reworked** – by purpose instead of by size: *for the Frame* (one size, each with its frame rate as measured: 0.75 M ≈ 36 fps, 1 M ≈ 35 fps and clearly sharper, 1.5 M ≈ 29 fps, 2 M ≈ 24 fps) or *for the PC* (all splats). One version per click, optionally the same *without cleaning up* next to it to compare. Before making, the dialog shows what it will make – and when that replaces an existing version.
  - Versions are named by what was cleaned up and how strongly (*all splats · ceiling 50 % · haze 30 %*), so two tries never look alike.
  - **As template** takes a version's settings into the form: change one slider and make the next.
  - A cleaned version for the PC keeps its own file for **Spirula Studio** and opens there with one click.
  - **Versions unfold under their scene or tour** (*▸ 3 versions*), with Spirula, copying to the Frame (with a warning above 2.5 M splats), template and delete.
  - **Tidier action bars** on scenes and tours: what you do (put together, copy to the Frame) – what you look at (Spirula, floor plan, folder) – what you change (rename, scale, clean up, delete, as icons with tooltips); the unfolding lists (*▸ 3 recordings*, *▸ 1 version* or *＋ Versions …*) on a line of their own. One versions button instead of two.
  - The PC sizes 3, 5 and 8 M are gone – on the PC all splats is what counts; any other size goes through *own size*.
- `scripts/freespace.py` (experimental, command line only): takes out splats in space the cameras looked through, from the recordings' sparse models. On a real living room it found only 0.4 % – white walls and ceilings have too few points to tell – but looked a little better; kept for trying.

## [0.3.1] – 2026-10-15

### Fixed

- **Folder names without a dot at the end**: a name like "Tour 12.10." gave the folder `Tour-12.10.`, which Windows file systems (NTFS, e.g. a second disk the runs are moved to) refuse – the copy silently lacked that tour. Now it is `Tour-12.10`. Existing folders keep their names.

## [0.3.0] – 2026-10-15

### Added

- **Rooms made of several recordings**: mark a group as *one room made of several recordings*, e.g. two short rounds through the living room. When it is put together, every part of the room comes from the recording that saw it best. Each recording still gets up to 500 image pairs, so several short recordings give a room far more detail than one long one.
- **Split a long video**: for a recording longer than its quality preset can use (500 image pairs – 100 s at *High*), Splat Workshop offers *Split the video* or *Convert in one piece*, each with its pros and cons. Split, it suggests as few parts as give the full rate of image pairs, all equally long (a 4-minute recording at *High*: 3 parts of 1:24 with 420 pairs each instead of 2 pairs per second), converts them one after the other and puts them together into one room. The parts are cut losslessly with the original time stamps (the pipeline matches images and poses by time); the original recording is never changed and is needed to cut again, so deleting it asks once more.
- **A home made of rooms**: a group may hold such rooms next to single scenes. Putting it together always starts from the original recordings in one go, each in its room: within a room every part from the best recording, between rooms the borders run through the doors as before. Nothing is reduced twice. The floor plan names and measures rooms instead of recordings, and the scale of a home reaches its rooms and their recordings.

- **Standby when done**: a checkbox on the running job. When everything is finished – also the following parts of a long recording, putting them together and copying to the Frame – the PC goes to standby after a minute's notice (a notification says so; untick to stop it). Cancelling a job switches it off: someone is there. If the standby is refused (e.g. another program keeps the PC awake), a notification says why.
- **One brightness for all recordings**: every training settles on its own brightness, so the same wall could come out a third darker in one recording than in the next. Putting them together now brings all recordings to one brightness per colour channel, from the places they share (`COLOUR=keep` for combine.sh leaves them as they are). The factors are in combine.json.

### Fixed

- **Dark holes where a recording had nothing**: a place went to the recording whose cameras looked at it from close by, even when its training had left next to nothing there (seen from behind furniture, too dark …). Now a place only goes to a recording that has at least a quarter of what the richest one has there (compared over the place and its neighbours). In a real living room of three parts, the places left thin dropped from 8.5 % to 1.9 %.
- **Kept awake between jobs**: every job keeps the PC awake while it runs, but between two of them (the parts of a long recording, then putting them together) there were a few seconds without – enough for the desktop to suspend a PC that has been idle for hours. Splat Workshop now holds one lock of its own for all of it, until a little after the last job.
- **A renamed room** gets its floor plan and preview images drawn again with the new name (before: the old name until it was put together again).
- Two rooms of the same name in a home are told apart as "Kitchen" and "Kitchen (2)" (was "Kitchen ·").
- The dialog for putting a room together explains rooms, not tours.
- A part of a long recording that failed or was cancelled keeps its piece of video for resuming; deleting the unfinished run now deletes that piece too (it is as big as that stretch of the recording and was in no list).
- Recordings of the same room no longer count as "walking in" to each other. Before, two overlapping recordings could hide the real walk-in of another room, which then claimed part of the room.

## [0.2.3] – 2026-10-14

### Added

- **Scale for models**, e.g. a model railway: set Z, N, TT, H0, S, 0, G or your own scale for a scene or a whole group (optionally passed on to its scenes). Floor plan, room sizes, the scale bar and the measuring tool then show the size of the original – a 10 cm H0 model reads as 8.7 m. The floor plan also looks for walls and floors in the model's scale, so a model is drawn like a real building.
- **Walk through a model in its original size** in Gaussian XR (option): Splat Workshop sets Gaussian XR's `worldScale` when copying a scene or tour to the Frame – the scene files on the PC stay unchanged. *Not tested on the Frame yet.*

- **Versions** of a scene or tour, several side by side: fewer splats for the Frame (0.75 / 1 / 1.5 / 2 M), all or a limit for the PC (3 / 5 / 8 M), or an own size – each also in the original's size for models. Every version gets its own place in the Frame's gallery (e.g. "Flat · 1 M" next to "Flat · 2 M"), so you can find out yourself what runs smoothly. Frame rates measured on a Steam Frame are shown for information.
- **Fetch several recordings from the Frame at once**: *Fetch all* (everything not on this PC yet) or tick recordings and *Fetch selection*. They come one after the other, with one progress bar for all and *Cancel*; recordings ticked while fetching join the queue. A notification at the end says when the Frame can be switched off; one that fails does not stop the others.
- **Delete a recording**, one at a time: on the Frame (for good – with a clear warning if it is not on this PC yet) or on this PC (to the trash). A recording that is being converted, checked or fetched, or that an unfinished scene still needs, stays.
- **"On the Frame" tab** under the finished scenes: everything in Gaussian XR's gallery on the Frame with its preview image – scenes, tours, versions and Gaussian XR's own examples – and *Off the Frame* to delete any of them there (the PC is not touched).

### Changed

- **Scenes go to Gaussian XR's folder for personal scenes** (`Documents/GaussianXR` on the Frame) instead of the app's own `Scenes` folder, which a Steam update or "verify files" can reset. They appear in the gallery after Gaussian XR's examples. Scenes copied by older versions move over when copied again, or all at once with *Move to own scenes* in the *On the Frame* tab; numbers of new folders count both places.
- **No backup copies on the Frame any more**: everything is on the PC, and *Copy again* restores a scene. Copies left in `Documents/GaussianSplats` by older versions are removed together with their scene.
- **1 M splats is the recommended size for the Frame** (preselected for versions and tours, also the default of `combine.sh`, was 3.5 M). Measured on a Steam Frame with one tour: 0.75 M 36 fps, 1 M 35 fps, 1.5 M 29 fps, 2 M 24 fps, 5 M 14 fps – 1 M looked clearly sharper than 0.75 M, 1.5 M a little sharper but already slightly unsteady. All sizes stay selectable.
- Putting a tour together offers 0.75 to 2 M splats or an own size for its Frame version (was 2 / 3.5 / 5 M).

- Tests extended after an independent review with mutation testing (499 including the type checks of every request field): scale, versions, fetching several recordings, deleting recordings, the *On the Frame* tab and the new folder, and what is locked while a scene is computed, checked, fetched or copied.

### Fixed

- The floor plan no longer fails for scenes in which it finds no walls (e.g. a model without its scale).
- The floor plan of a group uses the group's scale, also when it was not passed on to its scenes. Scenes in different scales without one for the group give a clear message instead of a plan in the wrong size.
- *Move to own scenes* waits until no scene is being copied to the Frame.

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

[0.4.2]: https://github.com/thunderheronight/splat-workshop/compare/v0.4.1...v0.4.2
[0.4.1]: https://github.com/thunderheronight/splat-workshop/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/thunderheronight/splat-workshop/compare/v0.3.1...v0.4.0
[0.3.1]: https://github.com/thunderheronight/splat-workshop/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/thunderheronight/splat-workshop/compare/v0.2.3...v0.3.0
[0.2.3]: https://github.com/thunderheronight/splat-workshop/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/thunderheronight/splat-workshop/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/thunderheronight/splat-workshop/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/thunderheronight/splat-workshop/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/thunderheronight/splat-workshop/releases/tag/v0.1.0
