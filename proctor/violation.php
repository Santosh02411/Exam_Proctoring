<?php
session_start();
header('Content-Type: application/json');
if ($_SERVER['REQUEST_METHOD'] !== 'POST') { http_response_code(405); exit; }
$body = json_decode(file_get_contents('php://input'), true);
$attempt_token = $body['attempt_token'] ?? null;
$test_id = intval($body['test_id'] ?? 0);
$reason = $body['reason'] ?? 'violation';
$ts = $body['ts'] ?? date('c');
$extra = $body['extra'] ?? [];

if (!$attempt_token || !$test_id) { http_response_code(400); echo json_encode(['error'=>'missing']); exit; }

$conn = new mysqli("localhost","root","","exam_proctoring");
$stmt = $conn->prepare("INSERT INTO proctor_violations (test_id, attempt_token, reason, ts, extra) VALUES (?, ?, ?, ?, ?)");
$extra_json = json_encode($extra);
$stmt->bind_param("issss", $test_id, $attempt_token, $reason, $ts, $extra_json);
$stmt->execute();
echo json_encode(['ok'=>1]);
