"""Enumerazione fotocamere e sessione di acquisizione live multipiattaforma."""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QObject, Signal

from .vision_engine import (
    _decode_barcodes_cross_platform,
    _vision_binary_path,
    run_apple_vision_on_image,
)


def list_mac_cameras() -> List[Dict[str, str]]:
    """Elenca le fotocamere disponibili sul computer (macOS AVFoundation, Windows DirectShow/WMI, Linux V4L2)."""
    if sys.platform == "darwin":
        bin_path = _vision_binary_path()
        if not bin_path:
            return []
        try:
            res = subprocess.run(
                [bin_path, "cameras"],
                capture_output=True,
                text=True,
                timeout=4,
            )
            if res.returncode == 0 and res.stdout.strip():
                raw = json.loads(res.stdout.strip())
                if isinstance(raw, list):
                    return [
                        {"id": str(c.get("id") or ""), "name": str(c.get("name") or "Fotocamera del computer")}
                        for c in raw
                        if isinstance(c, dict)
                    ]
        except Exception:
            pass
        return []

    if sys.platform == "win32":
        names: List[str] = []
        no_win = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            ps_cmd = (
                "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
                "Get-CimInstance Win32_PnPEntity | "
                "Where-Object { $_.PNPClass -in @('Camera','Image') -and $_.Status -eq 'OK' } | "
                "Select-Object -ExpandProperty Name"
            )
            res = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", ps_cmd],
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=4,
                creationflags=no_win,
            )
            if res.returncode == 0 and res.stdout.strip():
                for ln in res.stdout.splitlines():
                    nm = ln.strip()
                    if nm and nm not in names:
                        names.append(nm)
        except Exception:
            pass
        if names:
            return [{"id": str(i), "name": nm} for i, nm in enumerate(names)]
        # Fallback rapido DirectShow se WMI non ha restituito nomi
        try:
            import cv2  # type: ignore
            found_cams: List[Dict[str, str]] = []
            for idx in range(3):
                cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
                if cap is not None and cap.isOpened():
                    found_cams.append({"id": str(idx), "name": f"Fotocamera {idx + 1}"})
                    cap.release()
            return found_cams
        except Exception:
            return []

    # Linux (V4L2 /sys/class/video4linux)
    v4l_root = "/sys/class/video4linux"
    if os.path.isdir(v4l_root):
        linux_cams: List[Dict[str, str]] = []
        for entry in sorted(os.listdir(v4l_root)):
            if not entry.startswith("video"):
                continue
            num_str = entry[5:]
            if not num_str.isdigit() or not os.path.exists(f"/dev/{entry}"):
                continue
            idx_file = os.path.join(v4l_root, entry, "index")
            if os.path.isfile(idx_file):
                try:
                    with open(idx_file, "r", encoding="utf-8") as f:
                        if f.read().strip() != "0":
                            continue
                except Exception:
                    pass
            cam_name = f"Fotocamera ({entry})"
            name_file = os.path.join(v4l_root, entry, "name")
            if os.path.isfile(name_file):
                try:
                    with open(name_file, "r", encoding="utf-8", errors="replace") as f:
                        rd = f.read().strip()
                        if rd:
                            cam_name = rd
                except Exception:
                    pass
            linux_cams.append({"id": num_str, "name": cam_name})
        if linux_cams:
            return linux_cams

    try:
        import cv2  # type: ignore
        fallback_cams: List[Dict[str, str]] = []
        for idx in range(3):
            cap = cv2.VideoCapture(idx)
            if cap is not None and cap.isOpened():
                fallback_cams.append({"id": str(idx), "name": f"Fotocamera {idx + 1}"})
                cap.release()
        return fallback_cams
    except Exception:
        return []


class MacCameraSignals(QObject):
    """Segnali Qt thread-safe per lo streaming live della fotocamera del computer."""
    cameraStarted = Signal(str)
    frameReady = Signal(bytes, str)      # (jpeg_bytes, live_barcode_or_empty)
    ocrCaptured = Signal(object)         # dict {"barcodes": [...], "lines": [...]}
    cameraError = Signal(str)


class MacCameraSession:
    """Gestisce lo streaming live della fotocamera e il rilevamento codici su macOS, Windows e Linux."""

    def __init__(self) -> None:
        self.signals = MacCameraSignals()
        self._proc: Optional[subprocess.Popen] = None
        self._running = False
        self._ocr_requested = False
        self._session_token = 0
        self._lock = threading.Lock()

    def _safe_emit(self, sig, *args) -> None:
        try:
            sig.emit(*args)
        except RuntimeError:
            pass

    @property
    def is_running(self) -> bool:
        if sys.platform == "darwin":
            return self._running and self._proc is not None and self._proc.poll() is None
        return self._running

    def start(self, camera_id: str = "") -> None:
        self.stop()
        if sys.platform == "darwin":
            self._start_macos(camera_id)
        else:
            self._start_opencv(camera_id)

    def _start_macos(self, camera_id: str) -> None:
        bin_path = _vision_binary_path()
        if not bin_path:
            self._safe_emit(self.signals.cameraError, "Componente nativo vision_scanner non trovato.")
            return
        cmd = [bin_path, "livecam"]
        if camera_id.strip():
            cmd.append(camera_id.strip())
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )
        except Exception as exc:
            self._safe_emit(self.signals.cameraError, str(exc))
            return

        with self._lock:
            self._proc = proc
            self._running = True

        def _reader() -> None:
            try:
                assert proc.stdout is not None
                for raw_line in proc.stdout:
                    if not self._running or proc is not self._proc:
                        break
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line)
                    except Exception:
                        continue
                    mtype = msg.get("type")
                    if mtype == "ready":
                        cam_name = str(msg.get("camera") or msg.get("message") or "Fotocamera del computer")
                        self._safe_emit(self.signals.cameraStarted, cam_name)
                    elif mtype == "error":
                        self._safe_emit(self.signals.cameraError, str(msg.get("message") or "Errore fotocamera"))
                    elif mtype == "frame":
                        b64_jpg = str(msg.get("jpeg_b64") or msg.get("jpeg") or "")
                        barcodes = msg.get("barcodes") or []
                        first_bc = str(msg.get("barcode") or (barcodes[0] if barcodes else "") or "")
                        if b64_jpg:
                            try:
                                jpg_bytes = base64.b64decode(b64_jpg)
                                self._safe_emit(self.signals.frameReady, jpg_bytes, first_bc)
                            except Exception:
                                pass
                    elif mtype == "ocr_result":
                        self._safe_emit(self.signals.ocrCaptured, msg)
            except Exception as exc:
                if self._running and proc is self._proc:
                    self._safe_emit(self.signals.cameraError, str(exc))
            finally:
                for pipe in (proc.stdin, proc.stdout):
                    if pipe is not None:
                        try:
                            pipe.close()
                        except Exception:
                            pass

        threading.Thread(target=_reader, daemon=True).start()

    def _start_opencv(self, camera_id: str) -> None:
        try:
            import cv2  # type: ignore
        except ImportError:
            self._safe_emit(
                self.signals.cameraError,
                "Modulo fotocamera (opencv-python-headless) non installato.",
            )
            return

        cam_idx = int(camera_id.strip()) if camera_id.strip().isdigit() else 0
        with self._lock:
            self._session_token += 1
            my_token = self._session_token
            self._running = True
            self._ocr_requested = False

        def _cv_loop() -> None:
            cap = None
            try:
                backends = (
                    [cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_ANY]
                    if sys.platform == "win32"
                    else [getattr(cv2, "CAP_V4L2", cv2.CAP_ANY), cv2.CAP_ANY]
                )
                for api_pref in backends:
                    try:
                        c = cv2.VideoCapture(cam_idx, api_pref)
                        if c is not None and c.isOpened():
                            cap = c
                            break
                        if c is not None:
                            c.release()
                    except Exception:
                        continue

                if cap is None or not cap.isOpened():
                    with self._lock:
                        if self._session_token == my_token:
                            self._running = False
                    self._safe_emit(
                        self.signals.cameraError,
                        "Impossibile avviare la fotocamera. Verifica che sia collegata e che l'accesso alla fotocamera sia consentito nelle impostazioni di sistema.",
                    )
                    return

                cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
                cap.set(cv2.CAP_PROP_AUTOFOCUS, 1)

                started_emitted = False
                frame_no = 0
                last_bc = ""
                last_bc_ts = 0.0
                empty_streak = 0

                while True:
                    with self._lock:
                        if not self._running or self._session_token != my_token:
                            break
                        want_ocr = self._ocr_requested
                        if want_ocr:
                            self._ocr_requested = False

                    ok, frame = cap.read()
                    if not ok or frame is None or frame.size == 0:
                        empty_streak += 1
                        if empty_streak > 45:
                            self._safe_emit(
                                self.signals.cameraError,
                                "Nessun segnale video dalla fotocamera selezionata.",
                            )
                            break
                        time.sleep(0.05)
                        continue

                    empty_streak = 0
                    if not started_emitted:
                        started_emitted = True
                        self._safe_emit(self.signals.cameraStarted, f"Fotocamera {cam_idx + 1}")

                    frame_no += 1
                    now = time.time()

                    # Rilevamento codici a barre sul fotogramma reale non specchiato
                    if frame_no % 3 == 1:
                        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                        found = _decode_barcodes_cross_platform(gray)
                        if found:
                            last_bc = found[0]
                            last_bc_ts = now
                        elif now - last_bc_ts > 0.9:
                            last_bc = ""

                    if want_ocr:
                        import tempfile
                        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".jpg")
                        os.close(tmp_fd)
                        try:
                            cv2.imwrite(tmp_path, frame, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
                            ocr_res = run_apple_vision_on_image(tmp_path, mode="ocr")
                            self._safe_emit(self.signals.ocrCaptured, ocr_res)
                        finally:
                            try:
                                os.remove(tmp_path)
                            except OSError:
                                pass

                    # Orientamento reale naturale (non a specchio) per leggere correttamente testi e barcode
                    disp_frame = frame
                    h_px, w_px = disp_frame.shape[:2]
                    if w_px > 960:
                        scale = 960.0 / float(w_px)
                        disp_frame = cv2.resize(
                            disp_frame,
                            (960, max(1, int(h_px * scale))),
                            interpolation=cv2.INTER_AREA,
                        )
                    ok_enc, buf = cv2.imencode(".jpg", disp_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
                    if ok_enc:
                        self._safe_emit(self.signals.frameReady, buf.tobytes(), last_bc)

                    time.sleep(0.04)
            finally:
                if cap is not None:
                    try:
                        cap.release()
                    except Exception:
                        pass
                with self._lock:
                    if self._session_token == my_token:
                        self._running = False

        threading.Thread(target=_cv_loop, daemon=True).start()

    def request_ocr(self) -> bool:
        if sys.platform == "darwin":
            return self._send_macos_cmd("ocr")
        with self._lock:
            if not self._running:
                return False
            self._ocr_requested = True
            return True

    def request_scan_file(self, image_path: str) -> bool:
        if sys.platform == "darwin" and image_path:
            if "\n" in image_path or "\r" in image_path:
                return False
            return self._send_macos_cmd(f"scanfile {image_path}")
        return False

    def _send_macos_cmd(self, cmd: str) -> bool:
        with self._lock:
            proc = self._proc
            if not self._running or proc is None or proc.stdin is None or proc.poll() is not None:
                return False
            try:
                proc.stdin.write(cmd + "\n")
                proc.stdin.flush()
                return True
            except Exception:
                return False

    def stop(self) -> None:
        with self._lock:
            self._running = False
            self._session_token += 1
            proc = self._proc
            self._proc = None
        if proc is not None:
            try:
                if proc.stdin and proc.poll() is None:
                    proc.stdin.write("quit\n")
                    proc.stdin.flush()
            except Exception:
                pass
            if proc.poll() is None:
                try:
                    proc.terminate()
                except Exception:
                    pass
                try:
                    proc.wait(timeout=1.0)
                except Exception:
                    try:
                        proc.kill()
                        proc.wait(timeout=0.5)
                    except Exception:
                        pass
            for pipe in (proc.stdin, proc.stdout):
                if pipe is not None:
                    try:
                        pipe.close()
                    except Exception:
                        pass
