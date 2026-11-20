# Palace for the EM models

[Palace](https://awslabs.github.io/palace/) has no Windows build and no
published container image. This folder builds one from source with Docker,
following Palace's install guide (CMake superbuild), so the models written by
**Export › EM (Palace)** run on this machine with one double-click.

## Once: build the image

Needs Docker Desktop (WSL 2 backend), already on this machine.

```powershell
powershell -ExecutionPolicy Bypass -File "$env:APPDATA\FreeCAD\v1-1\Mod\PackageStudio\tools\palace\build_palace.ps1"
```

This builds Palace v0.18.1 (Ubuntu 24.04, GCC 13, OpenMPI, OpenBLAS; CPU
only) as the image `pkgdesigner/palace:0.18.1`. It takes 30-60 minutes; the
log is `build.log` here. Check it with:

```powershell
docker run --rm pkgdesigner/palace:0.18.1 --help
```

## Every model: run it

Each exported `<name>_palace` folder holds:

| File | |
|---|---|
| `run_palace.bat` | double-click: runs Palace in the image, then converts the result |
| `run_palace.ps1` | the same from PowerShell; `.\run_palace.ps1 -Np 4` for fewer processes |
| `run_palace.sh` | Linux / WSL with a native `palace`, else the same image |

Results:

- `palace.log` — Palace's output;
- `output/port-S.csv` — S-parameters as Palace writes them;
- `output/package.sNp` — the same as Touchstone (magnitude/angle, 50 Ω), for
  a circuit simulator, scikit-rf or a VNA tool.

## Memory

Docker gets half the PC's memory by default (16 GB here). A second-order
model of 500,000 tetrahedra (the ALL_LNA and 6_final examples) has about
3.5 million unknowns. If Palace stops for lack of memory, give Docker more in
`%UserProfile%\.wslconfig`:

```ini
[wsl2]
memory=26GB
```

then run `wsl --shutdown` and restart Docker Desktop. Element order 1 in the
EM tab needs roughly a fifth of the memory, for a first look.
