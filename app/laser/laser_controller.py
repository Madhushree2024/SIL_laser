from __future__ import annotations


class LaserController:
    def connect(self) -> dict:
        return {"connected": False, "mode": "dummy", "message": "Laser integration is not enabled"}

    def disconnect(self) -> dict:
        return {"connected": False, "mode": "dummy"}

    def send_job(self, path: str) -> dict:
        return {"accepted": False, "path": path, "message": "Export the vector file and send it via the laser workflow"}

    def start(self) -> dict:
        return {"running": False}

    def pause(self) -> dict:
        return {"paused": False}

    def stop(self) -> dict:
        return {"stopped": True}

    def status(self) -> dict:
        return {"connected": False, "mode": "dummy"}
