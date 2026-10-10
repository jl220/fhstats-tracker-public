"""Personal Rivals views. Values are supplied by the background runtime."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
import webbrowser
import math
import time
from datetime import datetime, timezone

from PIL import Image, ImageTk

from forza_tracker.localization import current_language
from forza_tracker.ui.theme import COLORS
from forza_tracker.ui.track_map_view_model import build_track_map_panel


def say(es: str, en: str) -> str:
    return es if current_language() == 'es' else en


def lap_time(milliseconds) -> str:
    if milliseconds is None:
        return '—'
    value = max(0, int(milliseconds))
    minutes, remainder = divmod(value, 60000)
    seconds, millis = divmod(remainder, 1000)
    return f'{minutes}:{seconds:02d}.{millis:03d}'


def live_clock_values(state, now):
    """Bounded visual interpolation; never used for saved laps or split deltas."""
    current = state.get('current') or {}
    sectors = current.get('sectors') or {}
    if (not state.get('telemetry_fresh') or current.get('validity') == 'invalid'
            or sectors.get('status') == 'invalid'):
        return None, 0
    clock = state.get('clock') or {}
    seconds = clock.get('seconds', current.get('current_lap_seconds'))
    if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds < 0:
        return None, 0
    elapsed = min(.1, max(0, now - clock['sampled_at'])) if clock.get('advancing') else 0
    return round((seconds + elapsed) * 1000), round(elapsed * 1000)


def sector_delta(milliseconds):
    if milliseconds is None:
        return '—', COLORS['muted']
    if milliseconds == 0:
        return '±0.000 s', COLORS['muted']
    return f'{milliseconds / 1000:+.3f} s', COLORS['accent'] if milliseconds < 0 else COLORS['danger']


def utc_stamp(value) -> str:
    try:
        instant = datetime.fromisoformat(str(value))
        if instant.utcoffset() is None:
            return '—'
        return instant.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
    except (TypeError, ValueError):
        return '—'


def validity_text(row: dict | None) -> str:
    if not row:
        return say('Sin intento completado', 'No completed attempt')
    if not row.get('completed'):
        return say('Incompleto', 'Incomplete')
    state = row.get('validity', 'unknown')
    label = {'clean': say('Limpia', 'Clean'), 'invalid': say('Inválida', 'Invalid')}.get(state, say('Limpia', 'Clean'))
    return label


def comparison_text(evaluation: dict | None) -> tuple[str, str]:
    data = evaluation or {}
    status = data.get('status')
    headline = {
        'no_snapshot': say('Sin snapshot para comparar', 'No snapshot available'),
        'not_comparable': say('Sin vuelta limpia comparable', 'No comparable clean lap'),
        'malformed_snapshot': say('Snapshot no comparable', 'Snapshot cannot be compared'),
        'outside_top50': say('Fuera del top 50 del snapshot', 'Outside the snapshot top 50'),
        'tied_cutoff': say('Empate con el corte del top 50', 'Tied at the top 50 cutoff'),
        'partial': say('Snapshot parcial · puesto no disponible', 'Partial snapshot · rank unavailable'),
    }.get(status, say('Aún no hay una mejor limpia', 'No clean personal best yet'))
    if status == 'estimated_rank':
        headline = say(f"≈ Puesto {data.get('estimated_rank')} en este snapshot", f"≈ Rank {data.get('estimated_rank')} in this snapshot")
        if data.get('rank_uncertain'):
            headline += say(' · posible empate', ' · possible tie')
    gap = data.get('gap_top50_ms')
    cutoff = data.get('cutoff_ms')
    detail = ''
    if cutoff is not None:
        detail = say('Corte top 50: ', 'Top 50 cutoff: ') + lap_time(cutoff)
    if gap is not None:
        detail += '  ·  ' + (say(f'{abs(gap) / 1000:.3f} s por detrás', f'{abs(gap) / 1000:.3f} s behind') if gap > 0
                              else say(f'{abs(gap) / 1000:.3f} s de margen', f'{abs(gap) / 1000:.3f} s ahead'))
    if status == 'tied_cutoff':
        detail += say(' · El desempate no está garantizado.', ' · Tie-breaking is not guaranteed.')
    return headline, detail.strip(' ·')


def history_rank_text(row: dict) -> str:
    """Compact comparison for this time against the currently available snapshot."""
    if not row.get('completed'):
        return say('Incompleto', 'Incomplete')
    if row.get('validity') != 'clean':
        return say('No válida', 'Not eligible')
    data = row.get('current_comparison') or {}
    status = data.get('status')
    if status == 'estimated_rank' and data.get('estimated_rank') is not None:
        label = f"≈{data['estimated_rank']}"
        if data.get('rank_uncertain'):
            label += say(' · empate', ' · tie')
        return label
    return {
        'outside_top50': say('Fuera del top 50', 'Outside top 50'),
        'tied_cutoff': say('Empate en top 50', 'Tied at top 50'),
        'partial': say('Snapshot parcial', 'Partial snapshot'),
        'malformed_snapshot': say('Snapshot no disponible', 'Snapshot unavailable'),
        'not_comparable': say('No comparable', 'Not comparable'),
    }.get(status, say('Sin snapshot', 'No snapshot'))


def _label(parent, text='', *, size=10, color='text', bg=None, **kwargs):
    return tk.Label(parent, text=text, bg=bg or parent.cget('bg'), fg=COLORS[color],
                    font=('Segoe UI Semibold' if size >= 13 else 'Segoe UI', size),
                    anchor='w', justify='left', **kwargs)


class RivalsLivePanel(tk.Frame):
    def __init__(self, parent, runtime, open_history):
        super().__init__(parent, bg=COLORS['panel'])
        self.runtime = runtime
        self._state = {}
        self._event_options = {}
        self._event_selection_key = None
        self._map_key = None
        self._photo = None
        self._mode = 'rivals'

        self.banner = _label(self, say('RIVALS · CAPTURA EN VIVO', 'RIVALS · LIVE CAPTURE'), color='accent')
        self.banner.pack(fill='x', pady=(0, 8))
        self.body = tk.Frame(self, bg=COLORS['panel'])
        self.body.pack(fill='x')
        header = tk.Frame(self.body, bg=COLORS['current'], padx=14, pady=14,
                          highlightbackground=COLORS['accent'], highlightthickness=1)
        header.pack(fill='x')
        header.columnconfigure(1, weight=1)
        self.map = _label(header, '◇', size=28, color='accent')
        self.map.grid(row=0, column=0, rowspan=3, padx=(0, 18))
        self.title = _label(header, say('Selecciona el evento', 'Select the event'), size=19)
        self.title.grid(row=0, column=1, sticky='ew')
        self.car = _label(header, say('Esperando coche y clase', 'Waiting for car and class'), color='muted')
        self.car.grid(row=1, column=1, sticky='ew', pady=(6, 4))
        self.context = _label(header, '', color='accent2')
        self.context.grid(row=2, column=1, sticky='ew')
        self.bind('<Configure>', self._resize, add='+')

        metrics = tk.Frame(self.body, bg=COLORS['panel'])
        metrics.pack(fill='x', pady=10)
        self.metrics = {}
        self.metric_titles = {}
        for index, (key, label) in enumerate([
            ('current', say('VUELTA ACTUAL', 'CURRENT LAP')),
            ('last', say('ÚLTIMA COMPLETADA', 'LAST COMPLETED')),
            ('best', say('MEJOR LIMPIA · ESTE COCHE', 'CLEAN BEST · THIS CAR')),
        ]):
            metrics.columnconfigure(index, weight=1, uniform='metric')
            card = tk.Frame(metrics, bg=COLORS['surface'], padx=12, pady=12)
            card.grid(row=0, column=index, sticky='nsew', padx=(0 if index == 0 else 8, 0))
            title = _label(card, label, color='muted', size=9)
            title.pack(fill='x')
            self.metric_titles[key] = title
            value = _label(card, '—', size=24, color='accent' if key == 'best' else 'text')
            value.pack(fill='x', pady=(5, 3))
            detail = _label(card, '', color='muted', size=9, wraplength=190)
            detail.pack(fill='x')
            self.metrics[key] = value, detail

        self.sector_panel = tk.Frame(self.body, bg=COLORS['panel2'], padx=12, pady=10)
        self.sector_panel.pack(fill='x', pady=(0, 10))
        _label(self.sector_panel, say('SECTORES · ÚLTIMA LIMPIA REGISTRADA', 'SECTORS · LAST RECORDED CLEAN LAP'), size=9, color='accent').pack(fill='x')
        self.sector_status = _label(self.sector_panel, '', size=9, color='muted')
        self.sector_status.pack(fill='x', pady=(3, 5))
        last_split = tk.Frame(self.sector_panel, bg=COLORS['panel2'])
        last_split.pack(fill='x', pady=(0, 5))
        self.last_split_label = _label(last_split, '', size=9, color='muted')
        self.last_split_label.pack(side='left')
        self.last_split_delta = _label(last_split, '', size=9, color='muted')
        self.last_split_delta.pack(side='left', padx=(8, 0))
        sector_cards = tk.Frame(self.sector_panel, bg=COLORS['panel2'])
        sector_cards.pack(fill='x')
        self.sector_widgets = []
        for index in range(3):
            sector_cards.columnconfigure(index, weight=1, uniform='sector')
            card = tk.Frame(sector_cards, bg=COLORS['surface'], padx=10, pady=6)
            card.grid(row=0, column=index, sticky='nsew', padx=(0 if index == 0 else 8, 0))
            _label(card, f'S{index+1}', size=9, color='muted').pack(side='left', padx=(0, 10))
            value = _label(card, '—', size=16)
            value.pack(fill='x')
            delta = _label(card, '—', size=10, color='muted')
            delta.pack(fill='x')
            reference = _label(card, '', size=8, color='muted')
            reference.pack(fill='x')
            self.sector_widgets.append((value, delta, reference))

        benchmark = tk.Frame(self.body, bg=COLORS['panel2'], padx=14, pady=13,
                             highlightbackground=COLORS['line'], highlightthickness=1)
        benchmark.pack(fill='x')
        self.benchmark = benchmark
        _label(benchmark, say('TU TIEMPO FRENTE AL SNAPSHOT', 'YOUR TIME AGAINST THE SNAPSHOT'), color='accent', size=9).pack(fill='x')
        self.rank = _label(benchmark, '', size=17)
        self.rank.pack(fill='x', pady=(5, 4))
        self.gap = _label(benchmark, '', color='accent2')
        self.gap.pack(fill='x')
        self.published_result = _label(benchmark, '', color='accent2')
        self.published_result.pack(fill='x', pady=(4, 0))
        self.references = _label(benchmark, '', color='muted')
        self.references.pack(fill='x', pady=(6, 0))
        self.snapshot_info = _label(benchmark, '', color='muted', size=9, wraplength=650)
        self.snapshot_info.pack(fill='x', pady=(6, 0))

        assisted = tk.Frame(self.body, bg=COLORS['panel'])
        self.assisted = assisted
        assisted.pack(fill='x', pady=(12, 0))
        assisted.columnconfigure(0, weight=1)
        _label(assisted, say('Evento sin correspondencia en el catálogo · puedes seleccionarlo para esta sesión.',
                            'Event not matched to the catalog · you can select it for this session.'), color='muted', size=9).grid(row=0, column=0, columnspan=2, sticky='w', pady=(0, 5))
        self.event = tk.StringVar()
        self.event_box = ttk.Combobox(assisted, textvariable=self.event, state='readonly')
        self.event_box.grid(row=1, column=0, sticky='ew', padx=(0, 8))
        self.event_button = ttk.Button(assisted, text=say('Confirmar evento', 'Confirm event'), command=self._select_event)
        self.event_button.grid(row=1, column=1)
        actions = tk.Frame(self.body, bg=COLORS['panel'])
        self.actions = actions
        actions.pack(fill='x', pady=(12, 0))
        ttk.Button(actions, text=say('Ver mi historial', 'My history'), command=open_history).pack(side='left', padx=(0, 6))
        self.refresh_button = ttk.Button(actions, text=say('Actualizar snapshot', 'Refresh snapshot'), command=self._refresh)
        self.refresh_button.pack(side='left', padx=(0, 6))
        ttk.Button(actions, text=say('Clasificación web ↗', 'Web leaderboard ↗'), command=lambda: webbrowser.open('https://fhstats.com/rivals')).pack(side='left')
        self.help = _label(self.body, '', size=9, color='warning', wraplength=650)
        self.help.pack(fill='x', pady=(10, 0))
        self._clock_job = self.after(33, self._tick_clock)

    def destroy(self):
        if self._clock_job is not None:
            self.after_cancel(self._clock_job)
            self._clock_job = None
        super().destroy()

    def _tick_clock(self):
        if self.winfo_ismapped() and self._mode == 'rivals':
            self._render_clock(self.runtime.snapshot(live_only=True))
        self._clock_job = self.after(33, self._tick_clock)

    def _render_clock(self, state):
        milliseconds, advance = live_clock_values(state, time.monotonic())
        self.metrics['current'][0].configure(text=lap_time(milliseconds) if self._mode == 'rivals' else '—')
        sectors = (state.get('current') or {}).get('sectors') or {}
        if sectors.get('status') == 'invalid':
            for value, delta, reference in self.sector_widgets:
                value.configure(text='—')
            return
        if milliseconds is not None and sectors.get('status') == 'tracking':
            for row, (value, delta, reference) in zip(sectors.get('sectors') or [], self.sector_widgets):
                if row.get('state') == 'running' and row.get('elapsed_ms') is not None:
                    value.configure(text=lap_time(row['elapsed_ms'] + advance))

    def _resize(self, event):
        width = max(280, event.width - 200)
        self.title.configure(wraplength=width)
        self.car.configure(wraplength=width)
        for widget in (self.help, self.snapshot_info, self.rank, self.gap):
            widget.configure(wraplength=max(250, event.width - 40))
        for value, detail in self.metrics.values():
            detail.configure(wraplength=max(130, event.width // 3 - 42))

    def _select_event(self):
        selected = self._event_options.get(self.event.get())
        if selected:
            self.runtime.select_event(selected['event_id'])

    def _refresh(self):
        event = self._state.get('current', {}).get('event')
        self.runtime.refresh_snapshot(event['discipline'] if event else 'dirt')

    def render(self, state: dict, mode='rivals'):
        self._state = state
        self._mode = mode
        if mode == 'waiting':
            self.banner.configure(text=say('ESPERANDO EVENTO · Online o Rivals aparecerán aquí', 'WAITING FOR EVENT · Online or Rivals will appear here'), fg=COLORS['muted'])
            self.body.pack_forget()
            return
        self.body.pack(fill='x')
        self.banner.configure(text=say('RIVALS · CAPTURA EN VIVO', 'RIVALS · LIVE CAPTURE') if mode == 'rivals'
                              else say('EVENTO NO DISPONIBLE', 'EVENT UNAVAILABLE'), fg=COLORS['accent'] if mode == 'rivals' else COLORS['warning'])
        current = state.get('current') or {}
        event = current.get('event')
        automatic = current.get('event_source') == 'memory'
        raw_event = current.get('raw_event') or {}
        if automatic:
            self.assisted.pack_forget()
        else:
            self.assisted.pack(fill='x', pady=(12, 0), before=self.actions)
        self._event_options = {f"{(e.get('name_es') if current_language() == 'es' else e.get('name_en')) or e['event_name']} · {e['discipline']}": e for e in state.get('events', [])}
        options = tuple(self._event_options)
        if tuple(self.event_box.cget('values')) != options:
            self.event_box.configure(values=options)
        selection_key = (event.get('event_id') if event else None,
                         raw_event.get('route_id'), raw_event.get('instance_id'), raw_event.get('name'))
        if selection_key != self._event_selection_key:
            self._event_selection_key = selection_key
            self.event.set(next((name for name, e in self._event_options.items() if event and e['event_id'] == event['event_id']), ''))
        title = ((event.get('name_es') if current_language() == 'es' else event.get('name_en')) or event['event_name']) if event else raw_event.get('name') or say('Esperando evento de Rivals', 'Waiting for Rivals event')
        self.title.configure(text=title)
        car = current.get('car_name') or current.get('car_display_name') or say('Esperando coche', 'Waiting for car')
        self.car.configure(text=f"{car}  ·  {current.get('car_class', '—')} {current.get('performance_index') or ''}")
        self.context.configure(text=say('Rivals detectado · evento automático', 'Rivals detected · automatic event') if mode == 'rivals' and automatic
                               else say('Rivals detectado · evento manual', 'Rivals detected · manual event') if mode == 'rivals' and event
                               else say('Rivals detectado · evento pendiente de identificar', 'Rivals detected · event not yet identified') if mode == 'rivals'
                               else say('Lectura de contexto no disponible', 'Context reading unavailable'))
        key = event['event_id'] if event else None
        if key != self._map_key:
            self._map_key = key
            panel = build_track_map_panel(event['event_name'], discipline=event['discipline']) if event else None
            self._photo = None
            if panel and panel.image_path:
                try:
                    with Image.open(panel.image_path) as original:
                        im = original.convert('RGB')
                    im.thumbnail((150, 95), Image.Resampling.LANCZOS)
                    self._photo = ImageTk.PhotoImage(im, master=self)
                except OSError:
                    pass
            self.map.configure(image=self._photo or '', text='' if self._photo else '◇')
        seconds = current.get('current_lap_seconds')
        sector_state = current.get('sectors') or {}
        invalid = current.get('validity') == 'invalid' or sector_state.get('status') == 'invalid'
        observing = mode == 'rivals' and state.get('telemetry_fresh') and seconds is not None and not invalid
        self.metrics['current'][0].configure(text=lap_time(round(seconds * 1000)) if observing else '—')
        current_validity = {'invalid': say('Vuelta inválida', 'Invalid lap'),
                            'clean': say('Limpia hasta ahora', 'Clean so far')}.get(current.get('validity'), say('Limpia hasta ahora', 'Clean so far'))
        self.metrics['current'][1].configure(text=say('Inválida · esperando vuelta limpia', 'Invalid · waiting for a clean lap') if invalid
                                            else current_validity if observing else say('Esperando telemetría', 'Waiting for telemetry'))
        self._render_sectors(sector_state, invalid=invalid, circuit=bool(event and event.get('event_kind') == 'circuit'))
        self._render_clock(state)
        last, best = state.get('last'), state.get('best')
        car_known = bool(current.get('car_ordinal') and current.get('car_class'))
        saved_bests = sorted(
            [row for row in state.get('bests', []) if event and row.get('event_id') == event['event_id']
             and row.get('completed') and row.get('validity') == 'clean' and row.get('time_ms') is not None],
            key=lambda row: (row.get('car_class', ''), row.get('car_name', '')),
        ) if not best and not car_known else []
        shown_bests = saved_bests[:3]
        self.metrics['last'][0].configure(text=lap_time(last.get('time_ms')) if last else '—')
        self.metrics['last'][1].configure(text=validity_text(last))
        self.metric_titles['best'].configure(text=say('MEJORES GUARDADAS · ESTE EVENTO', 'SAVED BESTS · THIS EVENT') if shown_bests
                                            else say('MEJOR LIMPIA · ESTE COCHE', 'CLEAN BEST · THIS CAR'))
        self.metrics['best'][0].configure(
            text='\n'.join(f"{row['car_class']} · {lap_time(row['time_ms'])}" for row in shown_bests) if shown_bests
                 else lap_time(best.get('time_ms')) if best else '—',
            font=('Segoe UI Semibold', 16 if shown_bests else 24),
        )
        best_detail = ('\n'.join(f"{row['car_class']} · {row['car_name']}" for row in shown_bests) if shown_bests
                       else validity_text(best) if best else say('Esperando coche y clase', 'Waiting for car and class') if not car_known
                       else say('Aún sin mejor limpia para este coche y clase', 'No clean best for this car and class yet'))
        if len(saved_bests) > len(shown_bests):
            remaining = len(saved_bests) - len(shown_bests)
            best_detail += say(f'\nY {remaining} más en tu historial', f'\nAnd {remaining} more in your history')
        self.metrics['best'][1].configure(text=best_detail)
        snapshot = state.get('snapshot') or {}
        published = snapshot.get('player_result')
        evaluation = best.get('current_comparison') if best else snapshot
        headline, detail = comparison_text(evaluation)
        if not best:
            headline = (say('Esperando coche y clase para buscar tu mejor vuelta', 'Waiting for car and class to find your best lap') if not car_known
                        else say('Aún sin mejor limpia para este coche y clase', 'No clean best for this car and class yet'))
        if shown_bests:
            headline = say('Tus mejores guardadas en este evento', 'Your saved bests at this event')
            detail = '\n'.join(f"{row['car_class']} · {row['car_name']} · {lap_time(row['time_ms'])} · {history_rank_text(row)}" for row in shown_bests)
        if published:
            record_text = say('Tu marca publicada: ', 'Your published time: ') + lap_time(published['time_ms'])
            record_text += f" · {published.get('car') or '—'} · {snapshot.get('car_class', '')}"
            record_text += say(f" · puesto {published['rank']} en el snapshot", f" · rank {published['rank']} in the snapshot")
            if not best and not shown_bests:
                headline = say(f"Tu marca en el snapshot · puesto {published['rank']}",
                               f"Your snapshot result · rank {published['rank']}")
                detail = say('Aún sin mejor limpia registrada por el tracker.', 'No clean best recorded by the tracker yet.')
            self.published_result.configure(text=record_text)
        else:
            self.published_result.configure(text='')
        self.rank.configure(text=headline)
        self.gap.configure(text=detail)
        data = evaluation or {}
        refs = data.get('references') or {}
        self.references.configure(text='   ·   '.join(f'Top {rank}: {lap_time(refs[f"top_{rank}_ms"])}' for rank in (1, 10, 25, 50) if f'top_{rank}_ms' in refs))
        captured = data.get('captured_at')
        meta = (say('Captura: ', 'Captured: ') + utc_stamp(captured)) if captured else say('Sin snapshot evaluado', 'No evaluated snapshot')
        meta += say(' · Cobertura parcial' if data.get('coverage') == 'partial' else ' · Estimación aproximada',
                    ' · Partial coverage' if data.get('coverage') == 'partial' else ' · Approximate estimate')
        if data.get('downloaded_at'):
            meta += say(' · Descargado: ', ' · Downloaded: ') + utc_stamp(data['downloaded_at'])
        if shown_bests:
            meta = say('Marcas guardadas por coche y clase; comparación con sus snapshots disponibles. Esperando el coche actual.',
                       'Saved times by car and class; compared with their available snapshots. Waiting for the current car.')
        refresh = state.get('refresh', {})
        refresh_state = refresh.get('status') if event and refresh.get('discipline') == event['discipline'] else None
        suffix = {'unchanged': say('Snapshot disponible sin cambios; el juego puede haber cambiado.', 'Available snapshot unchanged; the game leaderboard may have changed.'),
                  'changed': say('Snapshot actualizado.', 'Snapshot updated.'),
                  'error': say('Sin conexión: se conserva la caché.', 'Offline: previous cache retained.'),
                  'refreshing': say('Actualizando snapshot…', 'Refreshing snapshot…')}.get(refresh_state, say('Caché sin comprobar.', 'Cache not checked.'))
        self.snapshot_info.configure(text=meta + '\n' + suffix)
        self.event_button.configure(state='normal' if mode == 'rivals' else 'disabled')
        self.refresh_button.configure(state='normal' if refresh.get('status') != 'refreshing' else 'disabled')
        limitation = say('Limpia mientras no se detecte invalidación; inválida al detectarla. Los intentos incompletos no cuentan como récords.',
                         'Clean while no invalidation is detected; Invalid when detected. Incomplete attempts do not count as personal bests.')
        if event and event.get('event_kind') != 'circuit':
            limitation = say('Tramo: la llegada automática aún no está validada. No se guardarán finales automáticamente.',
                             'Point-to-point: automatic finish detection is not validated yet. Finishes will not be saved automatically.')
        if mode == 'unknown':
            limitation = {
                'scanning': say('Buscando el contexto de Rivals en Forza…', 'Looking for the Rivals context in Forza…'),
                'unsupported': say('Esta versión de Forza aún no tiene un lector compatible.', 'This Forza version does not have a compatible reader yet.'),
                'scan_failed': say('No se ha podido confirmar el contexto. El tracker volverá a comprobarlo.', 'The context could not be confirmed. The tracker will check again.'),
                'read_error': say('La lectura de Forza no está disponible. Los récords guardados se conservan.', 'Forza cannot currently be read. Saved records are retained.'),
            }.get(state.get('status'), say('Contexto perdido: esperando una lectura confirmada.', 'Context lost: waiting for a confirmed reading.'))
        if state.get('error'):
            limitation = say('No se pudo completar la lectura o la acción. Revisa el estado y vuelve a intentarlo.', 'Reading or action failed. Check the status and try again.')
        self.help.configure(text=limitation)

    def _render_sectors(self, state, *, invalid, circuit):
        if not circuit:
            self.sector_panel.pack_forget()
            return
        self.sector_panel.pack(fill='x', pady=(0, 10), before=self.benchmark)
        status = 'invalid' if invalid else state.get('status')
        text = {
            'invalid': say('Vuelta invalidada: parciales descartados hasta la siguiente limpia.', 'Invalid lap: splits discarded until the next clean lap.'),
            'waiting_finish': say('Esperando meta para sincronizar los sectores.', 'Waiting for the finish line to synchronize sectors.'),
            'learning': say('Aprendiendo checkpoints: completa una vuelta limpia.', 'Learning checkpoints: complete a clean lap.'),
            'paused': say('En pausa', 'Paused'),
        }.get(status, say('Diferencia medida al cerrar cada sector.', 'Measured difference at each sector boundary.'))
        count, progress = state.get('checkpoint_count'), state.get('checkpoint_progress')
        if not invalid and count and progress is not None:
            text += f'  ·  CP {progress}/{count}'
        self.sector_status.configure(text=text, fg=COLORS['danger'] if invalid else COLORS['muted'])
        split = state.get('last_split')
        split_index = split.get('index') if isinstance(split, dict) else None
        split_duration = split.get('duration_ms') if isinstance(split, dict) else None
        split_delta_ms = split.get('delta_ms') if isinstance(split, dict) else None
        split_visible = (
            not invalid and status == 'tracking'
            and isinstance(split_index, int) and 1 <= split_index <= 3
            and isinstance(split_duration, (int, float)) and math.isfinite(split_duration)
            and isinstance(split_delta_ms, (int, float)) and math.isfinite(split_delta_ms)
        )
        if split_visible:
            delta, color = sector_delta(split_delta_ms)
            self.last_split_label.configure(
                text=say('Último sector completado', 'Last completed sector')
                + f' · S{split_index} · {lap_time(split_duration)}',
                fg=COLORS['muted'],
            )
            self.last_split_delta.configure(text=delta, fg=color)
        else:
            self.last_split_label.configure(text='', fg=COLORS['muted'])
            self.last_split_delta.configure(text='', fg=COLORS['muted'])
        rows = state.get('sectors') or []
        for index, (value, delta_label, reference_label) in enumerate(self.sector_widgets):
            row = rows[index] if index < len(rows) else {}
            duration = row.get('duration_ms') if row.get('state') == 'completed' else row.get('elapsed_ms')
            delta, color = sector_delta(None if invalid else row.get('delta_ms'))
            value.configure(text=lap_time(None if invalid else duration), fg=color if row.get('delta_ms') is not None and not invalid else COLORS['text'])
            delta_label.configure(text=delta, fg=color)
            reference = row.get('reference_ms')
            reference_label.configure(text=(say('Referencia: ', 'Reference: ') + lap_time(reference)) if reference is not None else say('Sin referencia', 'No reference'))


class RivalsHistoryPanel(tk.Frame):
    def __init__(self, parent, runtime):
        super().__init__(parent, bg=COLORS['bg'], padx=12, pady=12)
        self.runtime = runtime
        self._state = {}
        self._signature = None
        self._rows = {}
        self._mode = 'bests'
        _label(self, say('Tus Rivals', 'Your Rivals'), size=23).pack(fill='x')
        _label(self, say('Tu mejor limpia con cada coche, clase y evento. Todos los intentos quedan guardados.',
                        'Your clean best for every car, class and event. Every attempt is saved.'), color='muted', wraplength=800).pack(fill='x', pady=(5, 12))
        self.summary = _label(self, '', color='accent')
        self.summary.pack(fill='x', pady=(0, 10))
        modes = tk.Frame(self, bg=COLORS['bg'])
        modes.pack(fill='x', pady=(0, 10))
        self.best_button = ttk.Button(modes, text=say('Mejores personales', 'Personal bests'), command=lambda: self._set_mode('bests'))
        self.best_button.pack(side='left', padx=(0, 6))
        self.attempt_button = ttk.Button(modes, text=say('Todos los intentos', 'All attempts'), command=lambda: self._set_mode('attempts'))
        self.attempt_button.pack(side='left', padx=(0, 6))
        ttk.Button(modes, text=say('Clasificación en FHStats ↗', 'Leaderboard on FHStats ↗'), command=lambda: webbrowser.open('https://fhstats.com/rivals')).pack(side='right')
        filters = tk.Frame(self, bg=COLORS['bg'])
        filters.pack(fill='x', pady=(0, 10))
        filters.columnconfigure(0, weight=3)
        filters.columnconfigure(2, weight=2)
        self.event = tk.StringVar(value=say('Todos los eventos', 'All events'))
        self.event_box = ttk.Combobox(filters, textvariable=self.event, state='readonly')
        self.event_box.grid(row=0, column=0, sticky='ew', padx=(0, 8))
        self.car_class = tk.StringVar(value=say('Todas las clases', 'All classes'))
        box = ttk.Combobox(filters, textvariable=self.car_class, state='readonly', width=17,
                           values=[say('Todas las clases', 'All classes'), 'D', 'C', 'B', 'A', 'S1', 'S2', 'R', 'X'])
        box.grid(row=0, column=1, padx=(0, 8))
        self.car = tk.StringVar()
        car_entry = ttk.Entry(filters, textvariable=self.car)
        car_entry.grid(row=0, column=2, sticky='ew')
        _label(filters, say('Buscar coche', 'Search car'), color='muted', size=9).grid(row=1, column=2, sticky='w')
        for var in (self.event, self.car_class, self.car):
            var.trace_add('write', lambda *_: self._render_rows())
        table = tk.Frame(self, bg=COLORS['bg'])
        table.pack(fill='both', expand=True)
        table.rowconfigure(0, weight=1)
        table.columnconfigure(0, weight=1)
        columns = [('event', say('Evento', 'Event'), 205), ('car', say('Coche', 'Car'), 190),
                   ('class', say('Clase · PI', 'Class · PI'), 85), ('time', say('Tiempo', 'Time'), 95),
                   ('rank', say('Puesto · snapshot', 'Rank · snapshot'), 165),
                   ('validity', say('Validez', 'Validity'), 140), ('date', say('Fecha UTC', 'Date UTC'), 140)]
        self.tree = ttk.Treeview(table, columns=[x[0] for x in columns], show='headings', height=8, selectmode='browse')
        for key, label, width in columns:
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, minwidth=75, anchor='w')
        self.tree.grid(row=0, column=0, sticky='nsew')
        scrollbar = ttk.Scrollbar(table, orient='vertical', command=self.tree.yview)
        scrollbar.grid(row=0, column=1, sticky='ns')
        horizontal = ttk.Scrollbar(table, orient='horizontal', command=self.tree.xview)
        horizontal.grid(row=1, column=0, sticky='ew')
        self.tree.configure(yscrollcommand=scrollbar.set, xscrollcommand=horizontal.set)
        self.tree.tag_configure('clean', foreground=COLORS['accent'])
        self.tree.tag_configure('invalid', foreground=COLORS['danger'])
        self.tree.tag_configure('unknown', foreground=COLORS['muted'])
        self.tree.bind('<<TreeviewSelect>>', lambda _: self._detail())
        self.empty = _label(self, '', color='muted', wraplength=780)
        self.empty.pack(fill='x', pady=(9, 0))
        detail = tk.Frame(self, bg=COLORS['panel'], padx=12, pady=12)
        detail.pack(fill='x', pady=(10, 0))
        self.detail = _label(detail, '', color='muted', wraplength=780)
        self.detail.pack(fill='x')
        self.bind('<Configure>', lambda e: self.detail.configure(wraplength=max(260, e.width - 55)), add='+')

    def _set_mode(self, mode):
        self._mode = mode
        self._render_rows()

    def render(self, state):
        signature = state.get('revision', 0)
        self._state = state
        if signature != self._signature:
            self._signature = signature
            events = sorted({self._event_name(a) for a in state.get('attempts', [])})
            self.event_box.configure(values=[say('Todos los eventos', 'All events'), *events])
            count = len(state.get('bests', []))
            records = say('récord limpio' if count == 1 else 'récords limpios', 'clean record' if count == 1 else 'clean records')
            self.summary.configure(text=f"{count} {records} · {len(state.get('attempts', []))} " + say('intentos', 'attempts'))
            self._render_rows()

    def _event_name(self, row):
        event = next((e for e in self._state.get('events', []) if e['event_id'] == row['event_id']), {})
        return event.get('name_es' if current_language() == 'es' else 'name_en') or row['event_name']

    def _render_rows(self):
        if not hasattr(self, 'tree'):
            return
        selection = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        self._rows = {}
        for row in self._state.get(self._mode, []):
            if self.event.get() != say('Todos los eventos', 'All events') and self._event_name(row) != self.event.get():
                continue
            if self.car_class.get() != say('Todas las clases', 'All classes') and row['car_class'] != self.car_class.get():
                continue
            if self.car.get().casefold() not in row.get('car_name', '').casefold():
                continue
            key = row['attempt_id']
            self._rows[key] = row
            concise_validity = validity_text({**row, 'observed_partial': False})
            self.tree.insert('', 'end', iid=key, values=(self._event_name(row), row['car_name'],
                f"{row['car_class']} {row['performance_index']}", lap_time(row.get('time_ms')),
                history_rank_text(row), concise_validity, utc_stamp(row['captured_at']).removesuffix(' UTC')), tags=(row['validity'],))
        if selection and selection[0] in self._rows:
            self.tree.selection_set(selection)
        self.empty.configure(text='' if self._rows else say('Todavía no hay marcas aquí. Completa un intento desde Inicio. Puedes revisar tus vueltas en Todos los intentos.',
                                                           'No times here yet. Complete an attempt from Home. Your laps appear under All attempts.'))
        self.best_button.configure(style='Accent.TButton' if self._mode == 'bests' else 'TButton')
        self.attempt_button.configure(style='Accent.TButton' if self._mode == 'attempts' else 'TButton')
        self._detail()

    def _detail(self):
        selection = self.tree.selection()
        row = self._rows.get(selection[0]) if selection else None
        if not row:
            self.detail.configure(text=say('Selecciona una marca para ver su comparación y su estado.', 'Select a time to see its comparison and its status.'))
            return
        original = row.get('original_comparison') or {}
        current = row.get('current_comparison') or {}
        original_title, _ = comparison_text(original)
        current_title, gap = comparison_text(current)
        source_text = say('Evento automático', 'Automatic event') if row.get('event_source') == 'memory' else say('Evento manual', 'Manual event')
        self.detail.configure(text=f"{validity_text(row)} · {source_text} · {say('tiempo UDP aproximado', 'approximate UDP time')}\n"
            + say('Evaluación original: ', 'Original evaluation: ') + original_title + f" · {utc_stamp(original.get('captured_at'))}\n"
            + say('Snapshot disponible: ', 'Available snapshot: ') + current_title + f" · {utc_stamp(current.get('captured_at'))}\n" + gap)
