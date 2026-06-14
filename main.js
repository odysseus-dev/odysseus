const { app, BrowserWindow } = require('electron');
const { spawn, exec } = require('child_process');
const waitOn = require('wait-on');
const path = require('path');

let mainWindow;
let pythonProcess;

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1200,
    height: 800,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true
    },
    title: 'Odysseus'
  });

  // Remove the default menu bar for a cleaner look
  mainWindow.setMenuBarVisibility(false);

  mainWindow.loadURL('http://localhost:7000');

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

function killProcess(pid) {
  if (process.platform === 'win32') {
    exec(`taskkill /pid ${pid} /t /f`, (err) => {
      if (err) {
        console.error(`Error killing process: ${err}`);
      }
    });
  } else {
    try {
      process.kill(-pid); // Kill process group on Unix
    } catch (e) {
      console.error(`Error killing process: ${e}`);
    }
  }
}

app.on('ready', () => {
  console.log('Starting Odysseus backend...');

  // Spawn the Python backend
  if (process.platform === 'win32') {
    pythonProcess = spawn('powershell.exe', ['-ExecutionPolicy', 'Bypass', '-File', 'launch-windows.ps1'], {
      cwd: __dirname,
      detached: false
    });
  } else {
    pythonProcess = spawn('bash', ['launch-linux.sh'], {
      cwd: __dirname,
      detached: true
    });
  }

  pythonProcess.stdout.on('data', (data) => console.log(`Backend: ${data}`));
  pythonProcess.stderr.on('data', (data) => console.error(`Backend error: ${data}`));

  // Wait for the backend to be ready
  waitOn({
    resources: ['tcp:localhost:7000'],
    timeout: 60000 // 60 seconds timeout
  }).then(() => {
    console.log('Backend is ready, opening window...');
    createWindow();
  }).catch((err) => {
    console.error('Error waiting for backend. Ensure port 7000 is available.', err);
    app.quit();
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('will-quit', () => {
  if (pythonProcess && pythonProcess.pid) {
    console.log('Shutting down backend process...');
    killProcess(pythonProcess.pid);
  }
});
