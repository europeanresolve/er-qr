# ER QR — European Resolve QR codes

Makes QR codes in the European Resolve brand: Resolve Black modules on Paper White, Amber or White, with the logo in the centre and an optional caption.

In the NGO workspace this repo lives at `it/qr` (symlink to `~/dev/github/ziohimself/er-qr`). Generated PNG/SVG files go in `qr_out/` (not versioned). Edit `qr.toml` for your codes and run `uv run er_qr.py` from this folder.

## First time

Install uv (once per computer):

- macOS / Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`
- Windows (PowerShell): `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`

You don't need to install Python or any libraries yourself. uv downloads the right Python version and the libraries the first time you run the script.

## Making codes

1. Edit `qr.toml`: one `[[codes]]` block per code, with a name, URL and caption.
2. In this folder, run:

   ```
   uv run er_qr.py
   ```

3. The codes appear in `qr_out/` as PNG (screens, documents) and SVG (print, banners). Each PNG is checked to make sure it scans.

Quick one-off code, keeping the other settings from `qr.toml`:

```
uv run er_qr.py --link newsletter https://european-resolve.org/newsletter "Subscribe"
```

Other options: `uv run er_qr.py --help`.

## Files

| File | What it is |
|---|---|
| `er_qr.py` | The script |
| `qr.toml` | URLs, captions, theme, logo settings |
| `logo.png` | The logo (dark tile with amber chevrons) |
| `pyproject.toml`, `uv.lock` | Libraries and their exact versions, managed by uv |
