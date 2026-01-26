<?php
session_start();
header('Content-Type: application/json');
if ($_SERVER['REQUEST_METHOD'] !== 'POST') { http_response_code(405); exit; }
$body = json_decode(file_get_contents('php://input'), true);
if (!$body) { http_response_code(400); echo json_encode(['error'=>'invalid']); exit; }

$attempt_token = $body['attempt_token'] ?? null;
$test_id = intval($body['test_id'] ?? 0);
$reason = $body['reason'] ?? 'snapshot';
$ts = $body['ts'] ?? date('c');
$image = $body['image'] ?? null;

if (!$attempt_token || !$test_id || !$image) { http_response_code(400); echo json_encode(['error'=>'missing']); exit; }

// image is dataURL "data:image/jpeg;base64,...."
if (preg_match('/^data:image\/(png|jpeg|jpg);base64,/', $image, $m)) {
  $ext = ($m[1] === 'png') ? 'png' : 'jpg';
  $data = base64_decode(preg_replace('#^data:image/\w+;base64,#i', '', $image));
  $dir = __DIR__ . '/../uploads/proctor_snapshots';
  if (!is_dir($dir)) mkdir($dir, 0750, true);
  $fname = $dir . '/' . time() . '_' . bin2hex(random_bytes(6)) . '.' . $ext;
  file_put_contents($fname, $data);

  // Insert metadata in DB: proctor_snapshots (id, test_id, attempt_token, path, ts, reason)
  $conn = new mysqli("localhost","root","","exam_proctoring");
  $stmt = $conn->prepare("INSERT INTO proctor_snapshots (test_id, attempt_token, path, ts, reason) VALUES (?, ?, ?, ?, ?)");
  $relpath = str_replace(__DIR__ . '/..', '', $fname); // or store full path
  $stmt->bind_param("issss", $test_id, $attempt_token, $relpath, $ts, $reason);
  $stmt->execute();

  echo json_encode(['ok'=>1]);
} else {
  http_response_code(400);
  echo json_encode(['error'=>'invalid_image']);
}
