const { app, BrowserWindow, Menu, Notification, dialog, ipcMain, shell } = require('electron');
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');
const http = require('http');

app.setName('Reup Studio');

let server;
let serverDiagnostics = '';
const port = 8765;

function packagedBackendPath() {
  return process.platform === 'win32'
    ? path.join(process.resourcesPath, 'backend', 'ToolDownloadVideoBackend', 'ToolDownloadVideoBackend.exe')
    : path.join(process.resourcesPath, 'backend', 'ToolDownloadVideoBackend');
}

function waitForServer() {
  return new Promise((resolve, reject) => {
    const started = Date.now();
    const ping = () => {
      const req = http.get(`http://127.0.0.1:${port}/api/health`, res => {
        if (res.statusCode === 200) return resolve();
        setTimeout(ping, 200);
      });
      req.on('error', () => Date.now() - started > 30000 ? reject(new Error('FastAPI server did not start')) : setTimeout(ping, 200));
    };
    ping();
  });
}
async function startServer() {
  const root = path.resolve(__dirname, '..');
  const bundledBackend = app.isPackaged ? packagedBackendPath() : '';
  if (bundledBackend && fs.existsSync(bundledBackend)) {
    server = spawn(bundledBackend, [], {
      cwd: path.dirname(bundledBackend),
      windowsHide: true,
      stdio: ['ignore', 'pipe', 'pipe'],
      env: { ...process.env, TDV_HOME: app.getPath('userData') },
    });
    server.stdout?.on('data', chunk => { serverDiagnostics += String(chunk); });
    server.stderr?.on('data', chunk => { serverDiagnostics += String(chunk); });
    server.on('error', error => { serverDiagnostics += `${error.message}\n`; });
    await waitForServer();
    return;
  }

  const localPython = process.platform === 'win32'
    ? [path.join(root, '.venv', 'Scripts', 'python.exe'), path.join(root, 'venv', 'Scripts', 'python.exe')].find(fs.existsSync)
    : [path.join(root, '.venv', 'bin', 'python3'), path.join(root, 'venv', 'bin', 'python')].find(fs.existsSync);
  const python = process.env.TDV_PYTHON || localPython || (process.platform === 'win32' ? 'py' : 'python3');
  const args = process.platform === 'win32' && !localPython && !process.env.TDV_PYTHON ? ['-3', '-m', 'uvicorn', 'backend.main:app', '--host', '127.0.0.1', '--port', String(port)] : ['-m', 'uvicorn', 'backend.main:app', '--host', '127.0.0.1', '--port', String(port)];
  server = spawn(python, args, { cwd: root, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
  server.stdout?.on('data', chunk => { serverDiagnostics += String(chunk); });
  server.stderr?.on('data', chunk => { serverDiagnostics += String(chunk); });
  server.on('error', error => { serverDiagnostics += `${error.message}\n`; });
  await waitForServer();
}
function createWindow() {
  const win = new BrowserWindow({ width: 1400, height: 900, minWidth: 1050, minHeight: 700,
    title: 'Reup Studio', icon: path.join(__dirname, 'renderer-react', 'src', 'assets', 'reup-studio-logo.png'),
    webPreferences: { preload: path.join(__dirname, 'preload.js'), contextIsolation: true, nodeIntegration: false } });
  win.loadFile(path.join(__dirname, 'renderer-dist', 'index.html'));
}
app.whenReady().then(async () => {
  try { Menu.setApplicationMenu(null); await startServer(); createWindow(); }
  catch (error) {
    const detail = serverDiagnostics.trim() || error.message;
    await dialog.showMessageBox({ type: 'error', title: 'FastAPI could not start', message: 'Không thể khởi động backend FastAPI.', detail });
    app.quit();
  }
});
app.on('before-quit', () => { if (server) server.kill(); });
ipcMain.handle('choose-directory', async () => (await dialog.showOpenDialog({ properties: ['openDirectory'] })).filePaths[0] || '');
ipcMain.handle('choose-file', async (_e, filters) => (await dialog.showOpenDialog({ properties: ['openFile'], filters })).filePaths[0] || '');
ipcMain.handle('save-file', async (_e, options) => (await dialog.showSaveDialog(options)).filePath || '');
ipcMain.handle('open-path', async (_e, p) => shell.openPath(p));
ipcMain.handle('notify', async (_e, { title, body }) => {
  if (BrowserWindow.getFocusedWindow() || !Notification.isSupported()) return false;
  new Notification({ title: title || 'Reup Studio', body: body || '' }).show();
  return true;
});
