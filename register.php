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
// Handle signup
if ($_SERVER["REQUEST_METHOD"] == "POST") {
    $name     = $_POST['name'];
    $email    = $_POST['email'];
    $phone    = $_POST['phone'];
    $role     = $_POST['role'];  // NEW: role from form
    $password = password_hash($_POST['password'], PASSWORD_BCRYPT);

}




?>