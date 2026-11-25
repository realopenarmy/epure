"""Tap-drill / clearance tables used to SUGGEST threads for plain modelled holes (user must confirm)."""
import math

IN = 25.4
# metric coarse: (designation, nominal mm, pitch mm, tap drill mm)
METRIC = [('M2', 2, 0.4, 1.6), ('M2.5', 2.5, 0.45, 2.05), ('M3', 3, 0.5, 2.5), ('M4', 4, 0.7, 3.3), ('M5', 5, 0.8, 4.2),
          ('M6', 6, 1.0, 5.0), ('M8', 8, 1.25, 6.8), ('M10', 10, 1.5, 8.5), ('M12', 12, 1.75, 10.2), ('M14', 14, 2.0, 12.0),
          ('M16', 16, 2.0, 14.0), ('M18', 18, 2.5, 15.5), ('M20', 20, 2.5, 17.5), ('M22', 22, 2.5, 19.5), ('M24', 24, 3.0, 21.0)]
# unified: (designation, major in, tpi, tap drill in, drill name)
UNC = [('#4-40', .112, 40, .0890, '#43'), ('#5-40', .125, 40, .1015, '#38'), ('#6-32', .138, 32, .1065, '#36'),
       ('#8-32', .164, 32, .1360, '#29'), ('#10-24', .190, 24, .1495, '#25'), ('#12-24', .216, 24, .1770, '#16'),
       ('1/4-20', .250, 20, .2010, '#7'), ('5/16-18', .3125, 18, .2570, 'F'), ('3/8-16', .375, 16, .3125, '5/16'),
       ('7/16-14', .4375, 14, .3680, 'U'), ('1/2-13', .500, 13, .4219, '27/64'), ('9/16-12', .5625, 12, .4844, '31/64'),
       ('5/8-11', .625, 11, .5312, '17/32'), ('3/4-10', .750, 10, .6562, '21/32'), ('7/8-9', .875, 9, .7656, '49/64'),
       ('1-8', 1.0, 8, .8750, '7/8')]
UNF = [('#4-48', .112, 48, .0935, '#42'), ('#5-44', .125, 44, .1040, '#37'), ('#6-40', .138, 40, .1130, '#33'),
       ('#8-36', .164, 36, .1360, '#29'), ('#10-32', .190, 32, .1590, '#21'), ('#12-28', .216, 28, .1820, '#14'),
       ('1/4-28', .250, 28, .2130, '#3'), ('5/16-24', .3125, 24, .2720, 'I'), ('3/8-24', .375, 24, .3320, 'Q'),
       ('7/16-20', .4375, 20, .3906, '25/64'), ('1/2-20', .500, 20, .4531, '29/64'), ('9/16-18', .5625, 18, .5156, '33/64'),
       ('5/8-18', .625, 18, .5781, '37/64'), ('3/4-16', .750, 16, .6875, '11/16'), ('7/8-14', .875, 14, .8125, '13/16'),
       ('1-12', 1.0, 12, .9219, '59/64')]
# ISO 273 clearance holes (fine, medium) mm
CLEAR_M = {'M2': (2.2, 2.4), 'M2.5': (2.7, 2.9), 'M3': (3.2, 3.4), 'M4': (4.3, 4.5), 'M5': (5.3, 5.5), 'M6': (6.4, 6.6),
           'M8': (8.4, 9.0), 'M10': (10.5, 11.0), 'M12': (13.0, 13.5), 'M14': (15.0, 15.5), 'M16': (17.0, 17.5),
           'M20': (21.0, 22.0), 'M24': (25.0, 26.0)}
TOL = 0.03   # mm


def clearance_match(dia):
    for k, (f, m) in CLEAR_M.items():
        if abs(dia - f) <= TOL: return f'{k} close-fit clearance'
        if abs(dia - m) <= TOL: return f'{k} normal-fit clearance'
    for k, (f, m) in CLEAR_M.items():
        if f - 0.15 <= dia <= m + 0.15: return f'near {k} clearance size'
    return None


def suggest(dia_mm, units):
    """Return thread options whose tap drill (or minor diameter) matches the hole."""
    opts = []
    for name, nom, p, td in METRIC:
        minor = nom - 1.0825 * p
        how = 'tap drill' if abs(dia_mm - td) <= TOL else 'minor dia' if abs(dia_mm - minor) <= TOL else None
        if how:
            opts.append(dict(system='metric', thread=f'{name}x{p:g}', callout=f'{name}x{p:g}-6H', pitch_mm=p, nominal_mm=nom,
                             match=how, drill=f'Ø{td:g}'))
    for fam, table in (('UNC', UNC), ('UNF', UNF)):
        for name, maj, tpi, td, dn in table:
            p = IN / tpi
            minor = maj * IN - 1.0825 * p
            how = 'tap drill' if abs(dia_mm - td * IN) <= TOL else 'minor dia' if abs(dia_mm - minor) <= TOL else None
            if how:
                opts.append(dict(system='inch', thread=f'{name} {fam}', callout=f'{name} {fam}-2B', pitch_mm=p,
                                 nominal_mm=maj * IN, match=how, drill=f'Ø{td:.4f}" ({dn})'))
    if any(o['match'] == 'tap drill' for o in opts):
        opts = [o for o in opts if o['match'] == 'tap drill']
    pref = 'inch' if units == 'in' else 'metric'
    opts.sort(key=lambda o: (o['system'] != pref, o['match'] != 'tap drill'))
    return opts


def default_thread_depth(hole_depth_mm, pitch_mm, nominal_mm, thru):
    if thru: return None
    d = hole_depth_mm - 3 * pitch_mm          # leave ~3 pitches of drill point/runout
    d = min(d, max(1.5 * nominal_mm, hole_depth_mm * 0.75)) if d > 1.5 * nominal_mm else d
    return max(round(d * 2) / 2, pitch_mm * 3)
