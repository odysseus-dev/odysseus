import fileinput

# Read app.py and replace the BASE_DIR line
for line in fileinput.input('app.py', inplace=True):
    if line.startswith('BASE_DIR = '):
        print('BASE_DIR = os.path.dirname(os.path.abspath(__file__)) + "/"')
    else:
        print(line, end='')
