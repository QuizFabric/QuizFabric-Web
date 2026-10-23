import tkinter as tk
from tkinter import messagebox, simpledialog
import requests
import json
import time
import threading
import os

# The dev server runs on 5710; the production wsgi.py runs on 80.
# run_production.bat sets QUIZFABRIC_SERVER accordingly.
SERVER_URL = os.environ.get('QUIZFABRIC_SERVER', 'http://localhost:5710')

def get_data():
    try:
        # verify=False: the GUI talks to localhost but the Let's Encrypt
        # certificate is issued for the public domain name.
        response = requests.get(f'{SERVER_URL}/gui_data', verify=False, timeout=5)
        if response.status_code == 200:
            return response.json()
        return []
    except:
        return []

def send_action(action):
    try:
        requests.post(f'{SERVER_URL}/gui_action', json=action, verify=False, timeout=5)
    except:
        pass

def info_callback(ip_str, data):
    if not ip_str:
        return
    ip = ip_str.split(' - ')[0]
    for item in data:
        if item['ip'] == ip:
            messagebox.showinfo("Info", f"IP: {ip}\nPage: {item['page']}\nLast Seen: {time.ctime(item['last_seen'])}")
            break

def ban_callback(ip_str):
    if not ip_str:
        return
    ip = ip_str.split(' - ')[0]
    send_action({'action': 'ban', 'ip': ip})

def send_popup_callback(ip_str):
    if not ip_str:
        return
    ip = ip_str.split(' - ')[0]
    msg = simpledialog.askstring("Send Message", "Enter message:")
    if msg:
        send_action({'action': 'send_popup', 'ip': ip, 'message': msg})

def run_gui():
    root = tk.Tk()
    root.title("IP Monitor")
    listbox = tk.Listbox(root, width=50)
    listbox.pack(fill=tk.BOTH, expand=True)
    button_frame = tk.Frame(root)
    button_frame.pack()
    info_button = tk.Button(button_frame, text="Info", command=lambda: info_callback(listbox.get(tk.ACTIVE), data))
    ban_button = tk.Button(button_frame, text="Ban", command=lambda: ban_callback(listbox.get(tk.ACTIVE)))
    send_button = tk.Button(button_frame, text="Send Message", command=lambda: send_popup_callback(listbox.get(tk.ACTIVE)))
    info_button.pack(side=tk.LEFT)
    ban_button.pack(side=tk.LEFT)
    send_button.pack(side=tk.LEFT)

    data = []

    def update_list():
        nonlocal data
        data = get_data()
        listbox.delete(0, tk.END)
        for item in data:
            listbox.insert(tk.END, f"{item['ip']} - {item['page']} - {time.ctime(item['last_seen'])}")
        root.after(1000, update_list)

    update_list()
    root.mainloop()

if __name__ == '__main__':
    run_gui()