"""Compact white/blue ttk controls with antialiased opaque rounded corners."""
import tkinter as tk
from tkinter import ttk


def install(root):
    style = ttk.Style(root)
    images = getattr(root, '_rounded_images', [])

    def surface(color, radius=8):
        # Opaque corners hide the rectangular theme backing; supersample for smooth edges.
        size = radius * 2 + 4
        image = tk.PhotoImage(master=root, width=size, height=size)
        rgb = tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))
        rows = []
        for y in range(size):
            row = []
            for x in range(size):
                inside = 0
                for sy in range(4):
                    for sx in range(4):
                        px, py = x + (sx + .5) / 4, y + (sy + .5) / 4
                        dx = max(radius - px, px - (size - radius), 0)
                        dy = max(radius - py, py - (size - radius), 0)
                        inside += dx * dx + dy * dy <= radius * radius
                row.append('#' + ''.join(f'{round(255 + (v - 255) * inside / 16):02x}' for v in rgb))
            rows.append('{' + ' '.join(row) + '}')
        image.put(' '.join(rows))
        images.append(image)
        return image

    for name, color, hover, disabled, text in (
        ('TButton', '#eef5ff', '#e0edff', '#f4f7fb', '#326395'),
        ('Primary.TButton', '#1687ff', '#0875ed', '#b9d9fa', '#ffffff'),
        ('Nav.TButton', '#ffffff', '#f0f7ff', '#ffffff', '#64748b'),
        ('Selected.Nav.TButton', '#e7f2ff', '#dcecff', '#f0f7ff', '#087bec'),
    ):
        item = 'BlueRound.' + name
        if item not in style.element_names():
            style.element_create(item, 'image', surface(color),
                                 ('disabled', surface(disabled)), ('active', surface(hover)),
                                 border=8, padding=0, sticky='nsew')
        style.layout(name, [(item, {'sticky': 'nsew', 'children': [
            ('Button.padding', {'sticky': 'nsew', 'children': [
                ('Button.label', {'sticky': 'nsew'})]})]})])
        style.configure(name, padding=(14, 7), foreground=text, background='#ffffff',
                        anchor='center', width=0, borderwidth=0)
        style.map(name, foreground=[('disabled', '#9baec2')])
    style.layout('TMenubutton', [('BlueRound.TButton', {'sticky': 'nsew', 'children': [
        ('Menubutton.padding', {'sticky': 'nsew', 'children': [
            ('Menubutton.label', {'sticky': 'nsew'})]})]})])
    style.configure('TMenubutton', padding=(14, 7), foreground='#326395', background='#ffffff')
    style.layout('Card.TFrame', [('Frame.padding', {'sticky': 'nsew'})])
    style.configure('Card.TFrame', padding=0, background='#ffffff')
    style.configure('TEntry', padding=(10, 5), borderwidth=1, relief='flat',
                    fieldbackground='#ffffff', bordercolor='#e1ebf5',
                    lightcolor='#e1ebf5', darkcolor='#e1ebf5')
    style.map('TEntry', fieldbackground=[('readonly', '#f5f9ff')],
              bordercolor=[('focus', '#1687ff')], lightcolor=[('focus', '#1687ff')],
              darkcolor=[('focus', '#1687ff')])
    root._rounded_images = images
