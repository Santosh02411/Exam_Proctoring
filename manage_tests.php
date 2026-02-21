<?php
session_start();
if (!isset($_SESSION['user_id']) || $_SESSION['role'] !== 'admin') {
    header("Location: login.html"); exit;
}
$conn = new mysqli("localhost","root","","exam_proctoring");
if ($conn->connect_error) die("DB err: ".$conn->connect_error);

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    if (isset($_POST['delete_test_id'])) {
        $did = (int)$_POST['delete_test_id'];
        // delete test (cascade should remove questions if FK set)
        $dstmt = $conn->prepare("DELETE FROM tests WHERE id = ?");
        $dstmt->bind_param("i", $did);
        if ($dstmt->execute()) {
            $message = "Test deleted.";
        } else {
            $message = "Unable to delete test: " . $dstmt->error;
        }
        $dstmt->close();
    }
    if (isset($_POST['toggle_test_id'])) {
        $tid = (int)$_POST['toggle_test_id'];
        // get current status
        $row = $conn->query("SELECT status FROM tests WHERE id=".$tid)->fetch_assoc();
        $new = ($row && $row['status'] === 'published') ? 'draft' : 'published';
        $ust = $conn->prepare("UPDATE tests SET status = ? WHERE id = ?");
        $ust->bind_param("si", $new, $tid);
        if ($ust->execute()) {
            $message = "Test status updated to {$new}.";
        } else {
            $message = "Unable to update status: " . $ust->error;
        }
        $ust->close();
    }
    if (isset($_POST['duplicate_test_id'])) {
        $dup = (int)$_POST['duplicate_test_id'];
        // duplicate basic test row (not copying questions to keep simple)
        $orig = $conn->query("SELECT test_id, title, description, duration_minutes, total_questions, passing_marks, status FROM tests WHERE id=".$dup)->fetch_assoc();
        if ($orig) {
            $new_code = $orig['test_id'] . '_COPY_' . time();
            $ist = $conn->prepare("INSERT INTO tests (test_id, title, description, duration_minutes, total_questions, passing_marks, status, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?)");
            $created_by = $_SESSION['user_id'];
            $ist->bind_param("sssiiiis", $new_code, $orig['title'], $orig['description'], $orig['duration_minutes'], $orig['total_questions'], $orig['passing_marks'], $orig['status'], $created_by);
            if ($ist->execute()) {
                $message = "Test duplicated. New test id: " . $ist->insert_id;
            } else {
                $message = "Duplicate failed: " . $ist->error;
            }
            $ist->close();
        }
    }
}
$tests = $conn->query("SELECT id, test_id, title, status, duration_minutes, total_questions, passing_marks, created_at FROM tests ORDER BY created_at DESC");

?>
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Manage Tests — Admin</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;700&display=swap" rel="stylesheet">



