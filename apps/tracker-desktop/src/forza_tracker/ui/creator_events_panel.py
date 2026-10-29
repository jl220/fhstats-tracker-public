"""Compact desktop community capture status, separate from official Rivals."""
from tkinter import ttk

from .rivals_panel import say, lap_time


class CreatorEventsPanel(ttk.Frame):
    def __init__(self,parent,runtime=None):
        super().__init__(parent,padding=12,style='Panel.TFrame')
        self.title=ttk.Label(self,style='PanelField.TLabel')
        self.title.pack(anchor='w')
        self.event=ttk.Label(self,style='PanelMuted.TLabel',wraplength=950)
        self.event.pack(anchor='w',pady=(4,0))
        self.status=ttk.Label(self,style='PanelMuted.TLabel',wraplength=950)
        self.status.pack(anchor='w',pady=(4,0))
        self.best=ttk.Label(self,style='PanelField.TLabel',wraplength=950)
        self.best.pack(anchor='w',pady=(4,0))
        self.rows=ttk.Treeview(self,columns=('car','class','time'),show='headings',height=4)
        self.rows.column('car',width=260,minwidth=100,stretch=True)
        self.rows.column('class',width=70,stretch=False)
        self.rows.column('time',width=160,stretch=False)
        self._rows_key=None
        self.queue=ttk.Label(self,style='PanelMuted.TLabel',wraplength=950)
        self.queue.pack(anchor='w',pady=(4,0))
        self.bind('<Configure>',self._resize)

    def _resize(self,event):
        for label in (self.event,self.status,self.best,self.queue):
            label.configure(wraplength=max(240,event.width-30))

    def render(self,state):
        self.title.configure(text=say('Eventos comunitarios','Creator Events'))
        event=state.get('event') or {}
        sprint=event.get('blueprint_guid')=='5ef7bf46-64cf-4001-866e-6fbd9fa14596'
        cross_country=event.get('blueprint_guid')=='ad7f991a-897c-419b-a366-8a815fd99be7'
        point_to_point=sprint or cross_country
        run_kind='CC' if cross_country else 'Sprint'
        name=event.get('name') or say('Sin evento identificado','No event identified')
        code=event.get('blueprint_code') or (state.get('current') or {}).get('blueprint_code')
        self.event.configure(text=f'{name} ({code})' if code else name)
        labels={
            'available':say('Seguimiento por memoria activo','Memory capture active'),
            'scanning':say('Buscando el evento activo','Finding the active event'),
            'profile_mismatch':say('El gamertag del juego no coincide con tu perfil del tracker','The game gamertag does not match your tracker profile'),
            'car_metadata_unavailable':say('Esperando identificar coche, clase y PI en memoria','Waiting to identify car, class and PI in memory'),
            'no_solo_race':say('Esperando una carrera de un jugador','Waiting for a single player race'),
            'no_active_blueprint':say('Evento seleccionado. Esperando a entrar en su carrera','Event selected. Waiting to enter its race'),
            'error':say('Captura detenida. Revisa el registro del tracker','Capture stopped. Check the tracker log'),
        }
        current=state.get('current') or {}
        text=labels.get(state.get('status'),say('Esperando una carrera compatible','Waiting for a compatible race'))
        if current.get('status')=='observing':
            validity=say('sucia','dirty') if current.get('dirty') else say('sin suciedad detectada','no dirtiness detected')
            if point_to_point and current['lap']>1:
                text=say('{kind} finalizado','{kind} finished').format(kind=run_kind)
            elif point_to_point:
                text=say('{kind}: {time}, {validity}','{kind}: {time}, {validity}').format(
                    kind=run_kind,time=lap_time(round(current['seconds']*1000)),validity=validity)
            else:
                text=say('Vuelta {lap}: {time}, {validity}','Lap {lap}: {time}, {validity}').format(
                    lap=current['lap'],time=lap_time(round(current['seconds']*1000)),validity=validity)
        self.status.configure(text=text)
        event_bests=[a for a in state.get('bests',[]) if a.get('blueprint_guid')==event.get('blueprint_guid')]
        self.rows.heading('car',text=say('Coche','Car'))
        self.rows.heading('class',text=say('Clase','Class'))
        self.rows.heading('time',text=say('Mejor marca limpia','Best clean time'))
        key=tuple((a['attempt_id'],a.get('car_name'),a['car_ordinal'],a['car_class'],a['time_ms']) for a in event_bests)
        if key!=self._rows_key:
            for item in self.rows.get_children():self.rows.delete(item)
            for attempt in event_bests:
                name=attempt.get('car_name') or str(attempt['car_ordinal'])
                self.rows.insert('', 'end',values=(name,attempt['car_class'],lap_time(attempt['time_ms'])))
            self._rows_key=key
        if event_bests:
            if not self.rows.winfo_manager():self.rows.pack(fill='x',pady=(6,0),before=self.queue)
            self.rows.configure(height=min(4,len(event_bests)))
        else:self.rows.pack_forget()
        bests=event_bests
        if current.get('car_ordinal'):
            bests=[a for a in bests if a['car_ordinal']==current['car_ordinal'] and a['car_class']==current.get('car_class')]
        best=min(bests,key=lambda a:a['time_ms']) if bests else None
        self.best.configure(text=(say('Mejor marca limpia observada: {time} ({car_class})','Best observed clean time: {time} ({car_class})').format(
            time=lap_time(best['time_ms']),car_class=best['car_class']) if best else say('Sin marca limpia completa guardada','No complete clean time saved')))
        sync_labels={
            'waiting':say('Envío automático con el gamertag de Forza. No necesitas iniciar sesión.', 'Automatic submission with your Forza gamertag. No sign in needed.'),
            'uploads_disabled':say('Envíos desactivados en Privacidad. Tus marcas se guardan localmente.', 'Uploads are disabled in Privacy. Results are saved locally.'),
            'claim_required':say('Tu gamertag necesita una tarjeta reclamada en FHStats. Se reintentará automáticamente.', 'Your gamertag needs a claimed FHStats card. Retrying automatically.'),
            'synced':say('Envío automático preparado. Los intentos recibidos quedan pendientes de revisión.', 'Automatic submission ready. Received attempts remain pending review.'),
            'local_pending':say('Hay marcas locales pendientes de catálogo o evidencia compatible.', 'Local results await compatible catalog entries or evidence.'),
            'retry_pending':say('Conexión pendiente. Se reintentará sin duplicar intentos.', 'Connection pending. Retrying without duplicate attempts.'),
            'sync_failed':say('Envío detenido. Tus marcas se conservan localmente.', 'Submission stopped. Results remain saved locally.'),
        }
        message=sync_labels.get(state.get('sync_status'),sync_labels['waiting'])
        self.queue.configure(text=say('{count} mejores marcas pendientes. {message}',
                                      '{count} best results pending. {message}').format(count=state.get('pending',0),message=message))
