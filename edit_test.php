<?php
session_start();
if (!isset($_SESSION['user_id']) || $_SESSION['role'] !== 'admin') {
    header("Location: login.html"); exit;
}
$conn = new mysqli("localhost","root","","exam_proctoring");
if ($conn->connect_error) die("DB err: ".$conn->connect_error);

// fetch test id
$test_id = isset($_GET['test_id']) ? (int)$_GET['test_id'] : 0;
if (!$test_id) {
    die("Missing test_id");
}
// handle POST update
$success = null;
$error = null;

?>