import pytest

from forza_tracker.ui import app


def test_removed_garage_cannot_be_navigated_to():
    assert 'garage' not in [page[0] for page in app.visible_page_specs()]
    assert app.normalize_page_id('garage') == 'home'
    assert app.normalize_page_id('Mi garaje') == 'home'
    assert app.normalize_page_id('My Garage') == 'home'


@pytest.mark.parametrize('language', ['en', 'es'])
def test_home_selects_timing_rules_and_copies_selected_code_without_login(language):
    # Tcl interpreters must be created and destroyed in their own process.
    # Otherwise later API worker garbage collection can finalize Tk objects
    # from a non-UI thread on Windows.
    import os
    from pathlib import Path
    import subprocess
    import sys
    import textwrap
    code = textwrap.dedent("""
        import tkinter as tk
        import sys
        from forza_tracker.localization import configure_language
        from forza_tracker.ui.community_home import CommunityHomePanel
        from forza_tracker.ui.creator_events_panel import CreatorEventsPanel
        from forza_tracker.ui.theme import configure_theme
        language=sys.argv[1]
        configure_language(language)
        root=tk.Tk();root.withdraw()
        try:
            configure_theme(root)
            home=CommunityHomePanel(root)
            assert '107181279' in home.code.cget('text')
            assert home.track.cget('text') == 'Matsuri Netsu'
            assert home.headline.cget('text') == (
                'Your car. Your tune. Your best time.' if language=='en'
                else 'Tu coche. Tu tuneo. Tu mejor tiempo.')
            assert str(home.track_selector.cget('state')) == 'readonly'
            assert str(home.track_selector.cget('takefocus')) == '1'
            assert home.track_selector_label.cget('text') == (
                'EventLab track' if language=='en' else 'Pista de EventLab')
            choices = tuple(home.track_selector.cget('values'))
            assert len(choices) == 4
            assert 'Matsuri Netsu' in choices[0] and 'Road' in choices[0]
            assert 'Kinkaku Ji S 4L' in choices[1] and 'Dirt' in choices[1]
            assert 'Off-Road Capabilities Test' in choices[2]
            assert 'Sportsland Sugawara' in choices[3]
            assert ('whole clean run' if language=='en' else 'recorrido limpio completo') in home.description.cget('text').lower()
            copied=[]
            home.clipboard_clear=lambda: copied.clear()
            home.clipboard_append=lambda value: copied.append(value)
            for index, name, event_code in (
                (0,'Matsuri Netsu','107181279'),
                (1,'Kinkaku Ji S 4L','437239865'),
                (2,'Off-Road Capabilities Test','219994682'),
                (3,'Sportsland Sugawara','477632104'),
            ):
                home.track_selector.current(index)
                home.track_selector.event_generate('<<ComboboxSelected>>')
                root.update()
                assert home.track.cget('text') == name
                assert home.code.cget('text') == event_code
                assert home.copy_button.cget('text') == (
                    'Copy EventLab code' if language=='en' else 'Copiar código de EventLab')
                home.copy_button.invoke()
                assert copied == [event_code]
                description=home.description.cget('text').lower()
                if index==1:
                    assert ('one complete clean lap is enough' if language=='en' else 'basta una vuelta limpia completa') in description
                    assert '4' in description
                if index==2:
                    assert ('whole clean run' if language=='en' else 'recorrido limpio completo') in description
                if index==3:
                    assert ('optional practice' if language=='en' else 'práctica opcional') in description
            text=' '.join(widget.cget('text') for widget in home._copy_labels).lower()
            for phrase in (('per car and class','memory only','automatically','gamertag','claimed','review')
                           if language=='en' else
                           ('por coche y clase','solo por memoria','automáticamente','gamertag','reclamada','revisión')):
                assert phrase in text
            assert not hasattr(home,'email')
            assert not hasattr(home,'password')
            panel=CreatorEventsPanel(root)
            panel.render(dict(current={},bests=[],pending=0,sync_status='waiting'))
            assert not hasattr(panel,'email')
            assert not hasattr(panel,'password')
            assert not hasattr(panel,'connect')
            assert panel.rows.winfo_manager()==''
            panel.render(dict(event={'blueprint_guid':'track'},current={},bests=[dict(
                blueprint_guid='track',attempt_id='one',car_ordinal=368,
                car_name='Acura Integra',car_class='B',time_ms=70034)],sync_status='synced'))
            assert panel.rows.winfo_manager()=='pack'
            assert panel.rows.item(panel.rows.get_children()[0],'values')[-1]=='1:10.034'
            panel.render(dict(event={'blueprint_guid':'5ef7bf46-64cf-4001-866e-6fbd9fa14596'},
                current=dict(status='observing',lap=1,seconds=12.34,dirty=False),bests=[]))
            assert '0:12.340' in panel.status.cget('text')
            assert 'Sprint' in panel.status.cget('text')
            assert str(panel.rows.heading('time')['text']) == (
                'Best clean time' if language=='en' else 'Mejor marca limpia')
            panel.render(dict(event={'blueprint_guid':'5ef7bf46-64cf-4001-866e-6fbd9fa14596'},
                current=dict(status='observing',lap=2,seconds=0.,dirty=False),bests=[]))
            assert ('finished' if language=='en' else 'finalizado') in panel.status.cget('text').lower()
            assert '0:00.000' not in panel.status.cget('text')
            panel.render(dict(event={'blueprint_guid':'ad7f991a-897c-419b-a366-8a815fd99be7'},
                current=dict(status='observing',lap=1,seconds=12.34,dirty=False),bests=[]))
            assert 'CC' in panel.status.cget('text') and '0:12.340' in panel.status.cget('text')
            panel.render(dict(event={'blueprint_guid':'ad7f991a-897c-419b-a366-8a815fd99be7'},
                current=dict(status='observing',lap=2,seconds=0.,dirty=False),bests=[]))
            assert ('finished' if language=='en' else 'finalizado') in panel.status.cget('text').lower()


        finally:root.destroy()
    """)
    source=Path(__file__).resolve().parents[1]/'apps/tracker-desktop/src'
    env={**os.environ,'PYTHONPATH':str(source)}
    completed=subprocess.run([sys.executable,'-c',code,language],env=env,capture_output=True,text=True,timeout=30)
    assert completed.returncode==0,completed.stdout+completed.stderr
