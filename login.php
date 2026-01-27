<?php
session_start();

// DB Connection
$servername = "localhost";
$username = "root";
$password = "";
$dbname = "exam_proctoring";

$conn = new mysqli($servername, $username, $password, $dbname);
if ($conn->connect_error) {
    die("DB Connection failed: " . $conn->connect_error);
}


// Handle login
if ($_SERVER["REQUEST_METHOD"] == "POST") {
    $email    = $_POST['email'];
    $password = $_POST['password'];

    $stmt = $conn->prepare("SELECT id, name, password, role, status FROM users WHERE email=? LIMIT 1");
    $stmt->bind_param("s", $email);
    $stmt->execute();
    $result = $stmt->get_result();

    if ($row = $result->fetch_assoc()) {
        if ($row['status'] != "active") {
            echo "⚠️ Account is not active!";
        } elseif (password_verify($password, $row['password'])){
        // Store session data
            $_SESSION['user_id'] = $row['id'];
            $_SESSION['name']    = $row['name'];
            $_SESSION['role']    = $row['role'];

            echo "✅ Login successful! Welcome " . $row['name'];
            / Redirect based on role
            if ($row['role'] == "admin") {
                header("Location: admin_dashboard.php");
            } else {
                header("Location: student_dashboard.php");
            }
            exit;
         } else {
            echo "❌ Invalid password!";
        }
    } else {
        echo "❌ No account found with that email!";
    }
}
?>