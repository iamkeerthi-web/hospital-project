import os
import tempfile
import unittest

from app import create_app


class SmartQueueBackendTestCase(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.app = create_app({"DATABASE_PATH": self.db_path, "TESTING": True})
        self.client = self.app.test_client()

    def tearDown(self):
        for suffix in ("", "-wal", "-shm"):
            path = self.db_path + suffix
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass

    def _admin_headers(self):
        res = self.client.post(
            "/api/admin/login",
            json={"username": "admin", "password": os.environ.get("DEFAULT_ADMIN_PASSWORD", "Admin@123")},
        )
        self.assertEqual(res.status_code, 200, res.data)
        token = res.get_json()["auth_token"]
        return {"Authorization": f"Bearer {token}"}

    def _staff_headers(self):
        res = self.client.post(
            "/api/admin/login",
            json={"username": "staff", "password": os.environ.get("DEFAULT_STAFF_PASSWORD", "Staff@123")},
        )
        self.assertEqual(res.status_code, 200, res.data)
        token = res.get_json()["auth_token"]
        return {"Authorization": f"Bearer {token}"}

    def test_01_health_and_frontend_served(self):
        res = self.client.get("/api/health")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json()["status"], "ok")

        with self.client.get("/") as front:
            self.assertEqual(front.status_code, 200)
            self.assertIn(b"Smart Queue Management System", front.data)

    def test_02_patient_register_login_and_details(self):
        reg = self.client.post(
            "/api/patients/register",
            json={
                "name": "Rahul Verma",
                "age": 34,
                "phone": "9876543210",
                "email": "rahul@example.com",
                "password": "Password123",
                "preferred_lang": "hi",
            },
        )
        self.assertEqual(reg.status_code, 201, reg.data)
        token = reg.get_json()["auth_token"]
        headers = {"Authorization": f"Bearer {token}"}

        login = self.client.post(
            "/api/patients/login",
            json={"identifier": "9876543210", "password": "Password123"},
        )
        self.assertEqual(login.status_code, 200)

        upd = self.client.put("/api/patients/me", headers=headers, json={"age": 35, "preferred_lang": "en"})
        self.assertEqual(upd.status_code, 200)
        self.assertEqual(upd.get_json()["patient"]["age"], 35)

    def test_03_departments_and_doctors_management(self):
        admin_h = self._admin_headers()

        dept_res = self.client.post(
            "/api/departments",
            headers=admin_h,
            json={"name": "Dermatology", "code": "DERM", "avg_consult_mins": 6},
        )
        self.assertEqual(dept_res.status_code, 201, dept_res.data)
        dept_id = dept_res.get_json()["department"]["id"]

        doc_res = self.client.post(
            "/api/doctors/register",
            json={
                "name": "Dr. Neha Singh",
                "specialization": "Dermatologist",
                "department_id": dept_id,
                "phone": "9123456789",
                "email": "neha@smartqueue.local",
                "password": "Password123",
            },
        )
        self.assertEqual(doc_res.status_code, 201, doc_res.data)
        doc_id = doc_res.get_json()["doctor"]["id"]
        doc_h = {"Authorization": f"Bearer {doc_res.get_json()['auth_token']}"}

        avail = self.client.patch(
            f"/api/doctors/{doc_id}/availability",
            headers=doc_h,
            json={"is_available": False},
        )
        self.assertEqual(avail.status_code, 200)
        self.assertFalse(avail.get_json()["doctor"]["is_available"])

        reassign = self.client.patch(
            f"/api/doctors/{doc_id}/department",
            headers=admin_h,
            json={"department_id": 1},
        )
        self.assertEqual(reassign.status_code, 200)
        self.assertEqual(reassign.get_json()["doctor"]["department_id"], 1)

    def test_04_smart_tokens_separate_queues_and_appointments(self):
        # Walk-in in General Medicine (dept 1)
        t1 = self.client.post(
            "/api/tokens",
            json={
                "name": "Walkin One",
                "age": 25,
                "phone": "9001112221",
                "spoken_lang": "en",
                "symptoms": "mild fever and cough",
                "department_id": 1,
            },
        )
        self.assertEqual(t1.status_code, 201, t1.data)
        tok1 = t1.get_json()["token"]
        self.assertTrue(tok1["token_number"].startswith("GEN-"))
        self.assertEqual(tok1["queue_position"], 1)

        # Walk-in in Cardiology (dept 2) -> independent queue position 1
        t2 = self.client.post(
            "/api/tokens",
            json={
                "name": "Cardio Patient",
                "age": 52,
                "phone": "9001112222",
                "spoken_lang": "en",
                "symptoms": "routine checkup",
                "department_id": 2,
            },
        )
        self.assertEqual(t2.status_code, 201, t2.data)
        tok2 = t2.get_json()["token"]
        self.assertTrue(tok2["token_number"].startswith("CARD-"))
        self.assertEqual(tok2["queue_position"], 1)

        # Check-in by phone
        ci = self.client.post("/api/tokens/checkin", json={"phone": "9001112221"})
        self.assertEqual(ci.status_code, 200, ci.data)
        self.assertEqual(ci.get_json()["token"]["status"], "checked_in")

        # Registered patient books appointment and checks in
        reg = self.client.post(
            "/api/patients/register",
            json={"name": "Appt User", "phone": "9001112223", "password": "Password123"},
        )
        p_headers = {"Authorization": f"Bearer {reg.get_json()['auth_token']}"}
        appt = self.client.post(
            "/api/appointments",
            headers=p_headers,
            json={
                "department_id": 1,
                "appointment_time": "2026-11-27 10:30",
                "symptoms": "headache",
            },
        )
        self.assertEqual(appt.status_code, 201, appt.data)
        appt_id = appt.get_json()["appointment"]["id"]

        appt_ci = self.client.post(f"/api/appointments/{appt_id}/checkin", headers=p_headers)
        self.assertEqual(appt_ci.status_code, 201, appt_ci.data)
        self.assertEqual(appt_ci.get_json()["token"]["token_type"], "appointment")

    def test_05_queue_progression_and_emergency_priority(self):
        staff_h = self._staff_headers()

        r1 = self.client.post(
            "/api/tokens",
            json={"name": "Normal A", "phone": "9888000001", "symptoms": "cold", "department_id": 1},
        )
        r2 = self.client.post(
            "/api/tokens",
            json={"name": "Normal B", "phone": "9888000002", "symptoms": "sprain", "department_id": 1},
        )
        t1 = r1.get_json()["token"]
        t2 = r2.get_json()["token"]
        self.assertEqual(t1["queue_position"], 1)
        self.assertEqual(t2["queue_position"], 2)

        # Patient cannot assign emergency priority
        unauth = self.client.patch(f"/api/queue/{t2['id']}/priority", json={"is_emergency": True})
        self.assertEqual(unauth.status_code, 401)

        # Staff escalates Normal B to Emergency priority -> moves to position 1
        esc = self.client.patch(
            f"/api/queue/{t2['id']}/priority",
            headers=staff_h,
            json={"is_emergency": True, "priority_level": 15},
        )
        self.assertEqual(esc.status_code, 200, esc.data)
        waiting = esc.get_json()["queue"]["waiting_list"]
        self.assertEqual(waiting[0]["token_number"], t2["token_number"])
        self.assertEqual(waiting[0]["position"], 1)
        self.assertTrue(waiting[0]["is_emergency"])

        # Call next patient -> serves escalated t2 first
        nxt = self.client.post("/api/queue/department/1/next", headers=staff_h)
        self.assertEqual(nxt.status_code, 200, nxt.data)
        self.assertEqual(nxt.get_json()["serving"]["token_number"], t2["token_number"])

        pos1 = self.client.get(f"/api/queue/position/{t1['token_number']}")
        self.assertEqual(pos1.status_code, 200)
        self.assertEqual(pos1.get_json()["current_serving_token"], t2["token_number"])
        self.assertEqual(pos1.get_json()["position"], 1)

    def test_06_admin_monitoring_stats_and_restart_persistence(self):
        admin_h = self._admin_headers()

        self.client.post(
            "/api/tokens",
            json={"name": "Persist Patient", "phone": "9777000001", "symptoms": "chest pain", "department_id": 2},
        )

        stats = self.client.get("/api/admin/stats", headers=admin_h)
        self.assertEqual(stats.status_code, 200, stats.data)
        self.assertGreaterEqual(stats.get_json()["summary"]["total_tokens"], 1)
        self.assertGreaterEqual(stats.get_json()["summary"]["active_emergencies"], 1)

        patients = self.client.get("/api/admin/patients", headers=admin_h)
        self.assertEqual(patients.status_code, 200)
        self.assertTrue(any(p["phone"] == "9777000001" for p in patients.get_json()["patients"]))

        # Simulate application restart against the same SQLite DB file
        restarted_app = create_app({"DATABASE_PATH": self.db_path, "TESTING": True})
        restarted_client = restarted_app.test_client()
        stats_after = restarted_client.get("/api/admin/stats", headers=admin_h)
        self.assertEqual(stats_after.status_code, 200)
        self.assertGreaterEqual(stats_after.get_json()["summary"]["total_tokens"], 1)


if __name__ == "__main__":
    unittest.main()
