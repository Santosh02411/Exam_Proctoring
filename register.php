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
      // Validate role (must be student or admin)
    if (!in_array($role, ['student', 'admin'])) {
        die("⚠️ Invalid role selected!");
    }
    // Check if email already exists
    $check = $conn->prepare("SELECT id FROM users WHERE email=?");
    $check->bind_param("s", $email);
    $check->execute();
    $check->store_result();
    if ($check->num_rows > 0) {
        echo "⚠️ Email already registered!";
    }else {
        $stmt = $conn->prepare("INSERT INTO users (user_id, name, email, password, role, phone) VALUES (?, ?, ?, ?, ?, ?)");
        $user_id = uniqid(strtoupper(substr($role,0,3))); // e.g. STUxxxx or ADMxxxx
        $stmt->bind_param("ssssss", $user_id, $name, $email, $password, $role, $phone);
        if ($stmt->execute()) {
            echo "✅ Registration successful as $role!";
        } else {
            echo "❌ Error: " . $stmt->error;
        }


}




?>