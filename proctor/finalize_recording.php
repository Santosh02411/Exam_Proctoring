<?php
// proctor/finalize_recording.php
session_start();
header('Content-Type: application/json');

if ($_SERVER['REQUEST_METHOD'] !== 'POST') { http_response_code(405); echo json_encode(['error'=>'method']); exit; }

$attempt_token = $_POST['attempt_token'] ?? null;
$test_id = isset($_POST['test_id']) ? (int)$_POST['test_id'] : 0;
$filename = $_POST['filename'] ?? null; // optional suggested name
$duration_seconds = isset($_POST['duration_seconds']) ? (int)$_POST['duration_seconds'] : null;
$uploaded_by = isset($_SESSION['user_id']) ? (int)$_SESSION['user_id'] : null;

if (!$attempt_token || !$test_id) { http_response_code(400); echo json_encode(['error'=>'missing']); exit; }

$baseDir = __DIR__ . '/../uploads/proctor_recordings';
$safeToken = preg_replace('/[^a-zA-Z0-9_\-]/', '_', substr($attempt_token, 0, 100));
$tempPath = $baseDir . "/{$safeToken}.part";

if (!file_exists($tempPath)) { http_response_code(404); echo json_encode(['error'=>'not_found']); exit; }

// create final filename
$ts = date('Ymd_His');
$suggest = $filename ? preg_replace('/[^a-zA-Z0-9_\-\._]/','_', $filename) : "rec_{$safeToken}_{$ts}.webm";
$finalPath = $baseDir . '/' . $suggest;

// move/rename
if (!rename($tempPath, $finalPath)) {
    // fallback: copy then unlink
    if (!copy($tempPath, $finalPath) || !unlink($tempPath)) {
        http_response_code(500); echo json_encode(['error'=>'move_failed']); exit;
    }
}

// store metadata in DB
$conn = new mysqli("localhost","root","","exam_proctoring");
if ($conn->connect_error) {
    // still respond ok but warn
    echo json_encode(['ok'=>1, 'path'=>$finalPath, 'warning'=>'db_connect_failed']);
    exit;
}

$size = filesize($finalPath);
$stmt = $conn->prepare("INSERT INTO proctor_recordings (test_id, attempt_token, path, filename, size, duration_seconds, uploaded_by) VALUES (?, ?, ?, ?, ?, ?, ?)");
$relpath = str_replace(__DIR__ . '/../', '', $finalPath); // store relative path
$stmt->bind_param("isssiii", $test_id, $attempt_token, $relpath, $suggest, $size, $duration_seconds, $uploaded_by);
$stmt->execute();

echo json_encode(['ok'=>1, 'path'=>$relpath, 'size'=>$size, 'record_id'=>$stmt->insert_id]);
exit;
