import json
import os

SESSION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "current_test_session.json")

class TestSessionManager:
    @staticmethod
    def save_session(start_time_epoch, file_path, tare_load, tare_disp, max_disp, sampling_rate, images_dir, output_folder="", sample_data=None):
        data = {
            "status": "RUNNING",
            "start_time_epoch": start_time_epoch,
            "file_path": file_path,
            "output_folder": output_folder,
            "images_dir": images_dir,
            "tare_load": tare_load,
            "tare_disp": tare_disp,
            "max_disp": max_disp,
            "sampling_rate": sampling_rate,
            "sample_data": sample_data or {}
        }
        try:
            with open(SESSION_FILE, "w") as f:
                json.dump(data, f, indent=4)
                f.flush()
                os.fsync(f.fileno())
        except Exception as e:
            print(f"Error saving session: {e}")

    @staticmethod
    def mark_completed():
        try:
            if os.path.exists(SESSION_FILE):
                os.remove(SESSION_FILE)
        except:
            pass

    @staticmethod
    def load_active_session():
        if not os.path.exists(SESSION_FILE):
            return None
        try:
            with open(SESSION_FILE, "r") as f:
                data = json.load(f)
            if data.get("status") == "RUNNING":
                return data
            return None
        except:
            return None
