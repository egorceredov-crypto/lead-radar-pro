import os
import shutil


class SessionManager:
    def __init__(self, sessions_dir: str = "sessions"):
        self.sessions_dir = sessions_dir
        os.makedirs(self.sessions_dir, exist_ok=True)

    def add_session_file(self, src_path: str, name: str | None = None) -> str:
        if name is None:
            name = os.path.basename(src_path)
        dest = os.path.join(self.sessions_dir, name)
        shutil.copy2(src_path, dest)
        return dest

    def remove_session(self, name: str) -> bool:
        p = os.path.join(self.sessions_dir, name)
        if os.path.exists(p):
            os.remove(p)
            return True
        return False

    def list_sessions(self) -> list:
        return [f for f in os.listdir(self.sessions_dir) if not f.startswith('.')]
