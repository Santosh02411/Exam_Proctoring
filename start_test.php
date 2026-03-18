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


$stmt2 = $conn->prepare("SELECT * FROM tests WHERE id=? LIMIT 1");
$stmt2->bind_param("i", $test_id);
$stmt2->execute();
$tres = $stmt2->get_result()->fetch_assoc();
$duration = (int)$tres['duration_minutes'];

$qstmt = $conn->prepare("SELECT id, question_text, option_a, option_b, option_c, option_d FROM questions WHERE test_id=?");
$qstmt->bind_param("i",$test_id);
$qstmt->execute();
$qres = $qstmt->get_result();
$questions = [];
while($r = $qres->fetch_assoc()) $questions[] = $r;
$attempt_token = bin2hex(random_bytes(12));
?>
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title><?=htmlspecialchars($tres['title'])?> — Proctored Test</title>
  <meta name="viewport" content="width=device-width,initial-scale=1">

<!-- TensorFlow.js core -->
<script src="https://cdn.jsdelivr.net/npm/@tensorflow/tfjs@4.11.0/dist/tf.min.js"></script>

<!-- BlazeFace model -->
<script src="https://cdn.jsdelivr.net/npm/@tensorflow-models/blazeface"></script>

<!-- then your own script that calls FaceMonitor -->
    <style>
    body{font-family:Inter,system-ui,Arial;margin:0;background:#f5f7fb;color:#071033}
    .wrap{max-width:1000px;margin:18px auto;padding:18px}
    .card{background:#fff;border-radius:10px;padding:16px;box-shadow:0 8px 24px rgba(6,15,34,0.06)}
    h1{margin:0 0 6px 0;font-size:20px}
    .meta{color:#6b7280;font-size:13px;margin-bottom:12px}
    #consentOverlay{position:fixed;inset:0;display:flex;align-items:center;justify-content:center;background:rgba(2,6,23,0.6);z-index:9999}
    #consentBox{width:92%;max-width:760px;background:#fff;border-radius:12px;padding:20px;box-shadow:0 18px 50px rgba(2,6,23,0.35)}
    








  </style>
</head>
<body>


  <script>

</script>


<script>


</script>


</body>
</html>