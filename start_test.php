<?php
session_start();
if (!isset($_SESSION['user_id'])) die("Login required");
$uid = $_SESSION['user_id'];

$conn = new mysqli("localhost","root","","exam_proctoring");
if ($conn->connect_error) die("DB err: ".$conn->connect_error);

$test_id = isset($_GET['test_id']) ? (int)$_GET['test_id'] : 0;
if (!$test_id) die("Invalid test");

$attempt_token = bin2hex(random_bytes(12));


$stmt = $conn->prepare("SELECT * FROM test_eligibility WHERE test_id=? AND student_id=?");
$stmt->bind_param("ii", $test_id, $uid);
$stmt->execute();
$stmt->store_result();
if ($stmt->num_rows === 0) die("You are not eligible for this test");

?>
