<?php
// proctor/alerts.php
session_start();
header('Content-Type: application/json');
if ($_SERVER['REQUEST_METHOD'] !== 'POST') { http_response_code(405); echo json_encode(['error'=>'method']); exit; }

$body = json_decode(file_get_contents('php://input'), true);
if (!$body) { http_response_code(400); echo json_encode(['error'=>'invalid_json']); exit; }

$attempt_token = $body['attempt_token'] ?? null;
$test_id = isset($body['test_id']) ? (int)$body['test_id'] : 0;
$code = $body['code'] ?? 'unknown';
$message = $body['message'] ?? '';
$meta = isset($body['meta']) ? json_encode($body['meta']) : null;
$severity = $body['severity'] ?? 'warning';
$ts = $body['ts'] ?? date('Y-m-d H:i:s');

if (!$attempt_token || !$test_id) { http_response_code(400); echo json_encode(['error'=>'missing']); exit; }

// Basic auth check: ensure user is logged in and owns this attempt (recommended)
// if (!isset($_SESSION['user_id'])) { http_response_code(403); echo json_encode(['error'=>'not_logged_in']); exit; }

$conn = new mysqli("localhost","root","","exam_proctoring");
if ($conn->connect_error) { http_response_code(500); echo json_encode(['error'=>'db']); exit; }

$stmt = $conn->prepare("INSERT INTO proctor_alerts (test_id, attempt_token, ts, severity, code, message, meta) VALUES (?, ?, ?, ?, ?, ?, ?)");
$stmt->bind_param("issssss", $test_id, $attempt_token, $ts, $severity, $code, $message, $meta);
$stmt->execute();
$id = $stmt->insert_id;
$stmt->close();

echo json_encode(['ok'=>1,'id'=>$id]);
