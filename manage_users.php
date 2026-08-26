<?php
session_start();
if (!isset($_SESSION['user_id']) || $_SESSION['role'] !== 'admin') {
    header("Location: login.html"); exit;
}
$conn = new mysqli("localhost","root","","exam_proctoring");
if ($conn->connect_error) die("DB err: ".$conn->connect_error);

// Handle toggle action
if (isset($_GET['action'], $_GET['uid']) && in_array($_GET['action'], ['activate','deactivate'])) {
    $action = $_GET['action'];
    $uid = (int)$_GET['uid'];
    $status = $action === 'activate' ? 'active' : 'inactive';
    $stmt = $conn->prepare("UPDATE users SET status=? WHERE id=?");
    $stmt->bind_param("si", $status, $uid);
    $stmt->execute();
    header("Location: manage_users.php");
    exit;
}

// fetch users (excluding self)
$users = $conn->query("SELECT id, user_id, name, email, role, status, created_at FROM users ORDER BY created_at DESC");
?>
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Manage Users — Admin</title>
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;700&display=swap" rel="stylesheet">
  <style>
    :root{
      --bg:#f6f8fb;
      --card:#ffffff;
      --muted:#6b7280;
      --primary:#0b6ef6;
      --success:#10b981;
      --danger:#ef4444;
      --radius:12px;
      --shadow: 0 8px 24px rgba(11,22,50,0.06);
      font-family: 'Inter', system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial;
      color-scheme: light;
    }
    *{box-sizing:border-box}
    body{
      margin:0;
      background:linear-gradient(180deg,var(--bg),#f3f6fa);
      padding:24px;
      -webkit-font-smoothing:antialiased;
      -moz-osx-font-smoothing:grayscale;
      color:#071033;
    }

    header{ display:flex; justify-content:space-between; align-items:center; gap:12px; margin-bottom:18px; }
    .title { display:flex; gap:12px; align-items:center; }
    .back{ text-decoration:none; color:var(--muted); font-weight:600; display:inline-flex; gap:8px; align-items:center; padding:8px 12px; border-radius:10px; background:var(--card); box-shadow:var(--shadow) }
    .back svg{ opacity:0.75 }
    h1{ margin:0; font-size:18px; }
    p.lead{ margin:0; color:var(--muted); font-size:13px }

    .controls { display:flex; gap:12px; align-items:center; }
    .search {
      display:flex; align-items:center; gap:8px; background:var(--card); padding:10px 12px; border-radius:12px; box-shadow:var(--shadow);
      min-width:260px;
    }
    .search input{ border:0; outline:0; font-size:14px; background:transparent; width:100%; color:#071033; }

    .card { background:var(--card); border-radius:var(--radius); padding:16px; box-shadow:var(--shadow); }
    .table-wrap { margin-top:14px; overflow:auto; }
    table { width:100%; border-collapse:collapse; min-width:820px; }
    thead th { text-align:left; font-size:13px; color:var(--muted); padding:12px 14px; position:sticky; top:0; background:linear-gradient(180deg, #fff, #fbfdff); z-index:2 }
    tbody td { padding:12px 14px; vertical-align:middle; border-bottom:1px solid #f1f5f9; font-size:14px; color:#071033; }

    tbody tr:hover td { background:linear-gradient(90deg,#fbfdff,#ffffff); }
    .col-actions { white-space:nowrap; width:170px; }

    .badge {
      display:inline-block; padding:6px 10px; border-radius:999px; font-size:12px; font-weight:700;
    }
    .badge.active { background:rgba(16,185,129,0.12); color:var(--success); }
    .badge.inactive { background:rgba(239,68,68,0.08); color:var(--danger); }
    .role { background:rgba(11,110,246,0.06); color:var(--primary); padding:6px 8px; border-radius:8px; font-weight:700; font-size:12px; }

    .action-btn {
      display:inline-flex; align-items:center; gap:8px; padding:8px 10px; border-radius:8px; text-decoration:none; font-weight:700; font-size:13px;
      border:1px solid rgba(7,16,51,0.06);
    }
    .action-btn.view { background:transparent; color:var(--primary); }
    .action-btn.toggle { background:transparent; color:#374151; border-color:rgba(55,65,81,0.06); }

    /* responsive */
    @media (max-width:900px) {
      .search { min-width:160px; }
      table { min-width:720px; }
    }
    @media (max-width:680px) {
      header{flex-direction:column;align-items:flex-start; gap:10px}
      table { min-width:620px; }
    }

    /* small text */
    .small { font-size:13px; color:var(--muted); }

    /* empty state */
    .empty { padding:28px; text-align:center; color:var(--muted); }

    /* confirmation dialog hint */
    .hint { font-size:12px; color:var(--muted) }
  </style>
</head>
<body>
  <header>
    <div class="title">
      <a class="back" href="admin_dashboard.php" title="Back to dashboard">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden><path d="M15 18l-6-6 6-6" stroke="#0b1220" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>
        Back
      </a>
      <div>
        <h1>Manage Users</h1>
        <p class="lead">Activate or deactivate user accounts</p>
      </div>
    </div>

    <div class="controls">
      <div class="search" role="search" aria-label="Search users">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden><path d="M21 21l-4.35-4.35" stroke="#94a3b8" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/><circle cx="11" cy="11" r="6" stroke="#94a3b8" stroke-width="1.6"/></svg>
        <input id="userSearch" type="search" placeholder="Search by name, email or ID...">
      </div>
    </div>
  </header>

  <main>
    <div class="card">
      <div class="small hint">Tip: Use the search box to quickly find a user. Click Activate/Deactivate to toggle account status (you will be asked to confirm).</div>

      <div class="table-wrap" style="margin-top:12px;">
        <table aria-label="Users table">
          <thead>
            <tr>
              <th style="width:48px">#</th>
              <th>Name</th>
              <th>Email</th>
              <th>Role</th>
              <th>Status</th>
              <th>Joined</th>
              <th class="col-actions">Action</th>
            </tr>
          </thead>
          <tbody id="usersTbody">
            <?php $i=1; while($u=$users->fetch_assoc()): ?>
              <tr data-search="<?= htmlspecialchars(strtolower($u['name'].' '.$u['email'].' '.$u['user_id'])) ?>">
                <td><?= $i++ ?></td>
                <td>
                  <div style="font-weight:700"><?= htmlspecialchars($u['name']) ?></div>
                  <div class="small"><?= htmlspecialchars($u['user_id']) ?></div>
                </td>
                <td><div style="max-width:280px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap"><?= htmlspecialchars($u['email']) ?></div></td>
                <td><span class="role"><?= htmlspecialchars($u['role']) ?></span></td>
                <td>
                  <?php if ($u['status'] === 'active'): ?>
                    <span class="badge active">Active</span>
                  <?php else: ?>
                    <span class="badge inactive">Inactive</span>
                  <?php endif; ?>
                </td>
                <td><div class="small"><?= htmlspecialchars($u['created_at']) ?></div></td>
                <td class="col-actions">
                  <a class="action-btn view" href="mailto:<?= htmlspecialchars($u['email']) ?>" title="Email user">Email</a>

                  <?php if ($u['status'] === 'active'): ?>
                    <a class="action-btn toggle" href="manage_users.php?action=deactivate&uid=<?= (int)$u['id'] ?>" data-action="deactivate" data-name="<?= htmlspecialchars($u['name']) ?>">Deactivate</a>
                  <?php else: ?>
                    <a class="action-btn toggle" href="manage_users.php?action=activate&uid=<?= (int)$u['id'] ?>" data-action="activate" data-name="<?= htmlspecialchars($u['name']) ?>">Activate</a>
                  <?php endif; ?>
                </td>
              </tr>
            <?php endwhile; ?>
          </tbody>
        </table>

        <?php if ($users->num_rows === 0): ?>
          <div class="empty">No users found.</div>
        <?php endif; ?>
      </div>
    </div>
  </main>

  <script>
    // client-side search
    const input = document.getElementById('userSearch');
    const tbody = document.getElementById('usersTbody');
    input.addEventListener('input', (e) => {
      const q = e.target.value.trim().toLowerCase();
      const rows = tbody.querySelectorAll('tr');
      let any = false;
      rows.forEach(r => {
        const s = r.getAttribute('data-search') || '';
        if (q === '' || s.indexOf(q) !== -1) {
          r.style.display = '';
          any = true;
        } else {
          r.style.display = 'none';
        }
      });
      // optional: show empty state when no matches
      const empty = document.querySelector('.empty');
      if (!any) {
        if (!empty) {
          const d = document.createElement('div');
          d.className = 'empty';
          d.textContent = 'No users match your search.';
          tbody.parentNode.appendChild(d);
        }
      } else {
        if (empty) empty.remove();
      }
    });

    // confirmation for activate/deactivate links
    document.addEventListener('click', function(e) {
      const a = e.target.closest('a[data-action]');
      if (!a) return;
      e.preventDefault();
      const action = a.getAttribute('data-action');
      const name = a.getAttribute('data-name') || 'this user';
      const confirmText = action === 'activate'
        ? `Are you sure you want to ACTIVATE ${name}?`
        : `Are you sure you want to DEACTIVATE ${name}? This will prevent them from logging in.`;
      if (confirm(confirmText)) {
        window.location.href = a.href;
      }
    });
  </script>
</body>
</html>
