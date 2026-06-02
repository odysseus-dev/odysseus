import subprocess
import os
import sys

def main():
    # Get the directory where this .exe is located
    if getattr(sys, 'frozen', False):
        base_dir = os.path.dirname(sys.executable)
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))
    
    pythonw_exe = os.path.join(base_dir, "venv", "Scripts", "pythonw.exe")
    desktop_py = os.path.join(base_dir, "desktop.py")
    
    if not os.path.exists(pythonw_exe):
        # Fallback to python.exe if pythonw is missing so we can show an error
        python_exe = os.path.join(base_dir, "venv", "Scripts", "python.exe")
        if os.path.exists(python_exe):
            subprocess.Popen(
                [python_exe, "-c", "print('Error: Odysseus is not fully installed. Run install.bat first!'); input('Press Enter to exit...')"], 
                creationflags=subprocess.CREATE_NEW_CONSOLE
            )
        return
        
    # Start desktop.py silently in the background
    CREATE_NO_WINDOW = 0x08000000
    subprocess.Popen([pythonw_exe, desktop_py], cwd=base_dir, creationflags=CREATE_NO_WINDOW)

if __name__ == "__main__":
    main()
