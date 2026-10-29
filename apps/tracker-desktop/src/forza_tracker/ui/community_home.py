"""Small, offline introduction to the community event testing workflow."""
import tkinter as tk
from tkinter import ttk

from .rivals_panel import say
from .theme import COLORS


class CommunityHomePanel(tk.Frame):
    def __init__(self, parent):
        super().__init__(parent, bg=COLORS['panel'], padx=18, pady=16,
                         highlightthickness=1, highlightbackground=COLORS['line'])
        self._copy_labels = []

        def label(text, color='text', size=10, bold=False):
            widget = tk.Label(self, text=text, bg=COLORS['panel'], fg=COLORS[color],
                              font=('Segoe UI Semibold' if bold else 'Segoe UI', size),
                              anchor='w', justify='left', wraplength=760)
            widget.pack(fill='x', pady=(0, 7))
            self._copy_labels.append(widget)
            return widget

        label(say('COMUNIDAD · VERSIÓN DE PRUEBA', 'COMMUNITY · TEST BUILD'), 'accent', 9, True)
        self.headline = label(say('Tu coche. Tu tuneo. Tu mejor tiempo.',
                                  'Your car. Your tune. Your best time.'), size=21, bold=True)
        self._tracks = (
            ('Matsuri Netsu', '107181279', say('Sprint Road', 'Road sprint'), say(
                'Sprint Road: cuenta el tiempo total del recorrido limpio completo, de salida a meta.',
                'Road sprint: your time is the total for the whole clean run, from start to finish.')),
            ('Kinkaku Ji S 4L', '437239865', say('Circuito Dirt', 'Dirt circuit'), say(
                'Circuito Dirt: cuenta tu mejor vuelta limpia completa. Basta una vuelta limpia completa, '
                'aunque el evento esté configurado con 4 vueltas.',
                'Dirt circuit: your best complete clean lap counts. One complete clean lap is enough, '
                'even though the event is configured for 4 laps.')),
            ('Off-Road Capabilities Test', '219994682', say('Campo a través', 'Cross Country'), say(
                'Campo a través: cuenta el tiempo total del recorrido limpio completo, de salida a meta.',
                'Cross Country: your time is the total for the whole clean run, from start to finish.')),
            ('Sportsland Sugawara', '477632104', say('Práctica opcional', 'Optional practice'), say(
                'Práctica opcional en circuito Road: cuenta tu mejor vuelta limpia completa.',
                'Optional practice on a Road circuit: your best complete clean lap counts.')),
        )
        self.track_selector_label = label(say('Pista de EventLab', 'EventLab track'), 'muted', 9)
        self.track_selector = ttk.Combobox(
            self, state='readonly', takefocus=True, width=46,
            values=tuple(f'{name} · {kind}' for name, _, kind, _ in self._tracks))
        self.track_selector.pack(fill='x', pady=(0, 8))
        self.track_selector.bind('<<ComboboxSelected>>', self._select_track)
        self.track = label('', size=14, bold=True)
        self.description = label('', 'muted')
        self.code = label('', 'accent', 16, True)
        self.copy_button = ttk.Button(self, text=say('Copiar código de EventLab', 'Copy EventLab code'),
                                      command=self._copy_code)
        self.copy_button.pack(anchor='w', pady=(0, 10))
        label(say(
            'Elige coche y tuneo dentro de su clase; no necesitas alcanzar el PI máximo. '
            'Se guarda tu mejor tiempo por coche y clase.',
            'Choose your own car and tune within its class; reaching the maximum PI is optional. '
            'Your best time is saved per car and class.'), size=10)
        label(say(
            '1. Abre EventLab y busca el código. Entra en la carrera de un jugador.\n'
            '2. Deja el tracker abierto y completa el recorrido o una vuelta limpia según la pista.\n'
            '3. Tus marcas se envían automáticamente con el gamertag observado en Forza. '
            'Necesitas tu tarjeta reclamada en FHStats y revisión para competir.',
            '1. Open EventLab and search for the code. Enter the single player race.\n'
            '2. Keep the tracker open and complete the run or a clean lap according to the track.\n'
            '3. Times upload automatically with the gamertag observed in Forza. '
            'A claimed FHStats player card and review are required to compete.'), size=10)
        label(say(
            'Seguimiento automático solo por memoria. El botón de captura es para Online y vídeos.',
            'Automatic tracking uses memory only. The capture button is for Online and videos.'), 'muted', 9)
        self.track_selector.current(0)
        self._select_track()
        self.bind('<Configure>', self._resize)

    def _select_track(self, event=None):
        name, code, _, description = self._tracks[self.track_selector.current()]
        self.track.configure(text=name)
        self.code.configure(text=code)
        self.description.configure(text=description)
        self.copy_button.configure(text=say('Copiar código de EventLab', 'Copy EventLab code'))

    def _resize(self, event):
        for widget in self._copy_labels:
            widget.configure(wraplength=max(240, event.width - 40))

    def _copy_code(self):
        code = self.code.cget('text')
        try:
            self.clipboard_clear()
            self.clipboard_append(code)
            self.copy_button.configure(text=say('Código copiado', 'Code copied'))
        except tk.TclError:
            self.copy_button.configure(text=say(f'Código: {code}', f'Code: {code}'))
