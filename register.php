<?php
// Database connection
$servername = "localhost";
$username = "root";
$password = "";
$dbname = "exam_proctoring";

$conn = new mysqli($servername, $username, $password, $dbname);
if ($conn->connect_error) {
    die("DB Connection failed: " . $conn->connect_error);
}







?>