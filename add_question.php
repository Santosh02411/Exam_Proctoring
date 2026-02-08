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

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    $qtext = $_POST['question_text'];
    $a = $_POST['option_a'];
    $b = $_POST['option_b'];
    $c = $_POST['option_c'];
    $d = $_POST['option_d'];
    $correct = $_POST['correct_answer'];
    $marks = (int)$_POST['marks'];

    $stmt = $conn->prepare("INSERT INTO questions (test_id, question_text, option_a, option_b, option_c, option_d, correct_answer, marks) VALUES (?, ?, ?, ?, ?, ?, ?, ?)");
    $stmt->bind_param("issssssi", $test_db_id, $qtext, $a, $b, $c, $d, $correct, $marks);
    if ($stmt->execute()) echo "Question added.<br>";
    else echo "Err: ".$stmt->error;
}

?>