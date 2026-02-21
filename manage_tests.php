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
   
}



?>