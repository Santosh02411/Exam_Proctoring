<?php
session_start();
if (!isset($_SESSION['user_id']) || $_SESSION['role'] !== 'admin') die("Access denied");

$conn = new mysqli("localhost","root","","exam_proctoring");
if ($conn->connect_error) die("DB err: ".$conn->connect_error);

$test_db_id = isset($_GET['test_db_id']) ? (int)$_GET['test_db_id'] : null;
if (!$test_db_id) {
    echo "Provide test_db_id";
    exit;
}
?>