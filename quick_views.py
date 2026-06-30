# -*- coding: utf-8 -*-

bl_info = {
    "name": "Quick Views",
    "author": "Triceptacion",
    "version": (1, 0, 0),
    "blender": (2, 79, 0),
    "location": "View3D",
    "description": "ViewCube 6 vistas + Multi-vista - Auto Inicio integrado",
    "category": "3D View",
}

import bpy
import bgl
import blf
import math
from bpy.app.handlers import persistent

# --- Variables de Estado e Interfaz ---
_draw_handle = None

# Diccionarios por área (key = area.as_pointer())
_modal_areas = {}           # area_key -> True
_area_rects = {}            # area_key -> {'front': rect, 'left': rect, ...}

# Estados por área
_area_active_view = {}        # area_key -> 'FRONT' | 'BACK' | 'LEFT' | ...
_area_saved_data = {}         # area_key -> (location, rotation, distance, lock_state)
_area_waiting_restore = {}    # area_key -> bool
_area_highlight = {}          # area_key -> 'UNDO' | 'DELETE' | 'REDO' | None
_area_highlight_timer = {}    # area_key -> int


# ----------------------------------------------------------
# UTILIDADES DE CONTROL DE VISTA
# ----------------------------------------------------------

def get_region_3d(context):
    if context.area and context.area.type == 'VIEW_3D':
        return context.space_data.region_3d
    return None


def save_current_view(context, area_key):
    r3d = get_region_3d(context)
    if r3d:
        _area_saved_data[area_key] = (
            r3d.view_location.copy(),
            r3d.view_rotation.copy(),
            r3d.view_distance,
            context.space_data.lock_camera
        )
        return True
    return False


def restore_saved_view(context, area_key):
    data = _area_saved_data.get(area_key)
    if data:
        r3d = get_region_3d(context)
        if r3d:
            loc, rot, dist, lock = data
            r3d.view_location = loc
            r3d.view_rotation = rot
            r3d.view_distance = dist
            r3d.view_perspective = 'PERSP'
            context.space_data.lock_camera = lock if lock else False
            _area_waiting_restore[area_key] = False
            return True
    return False


def toggle_front_back_view(context, area_key):
    active = _area_active_view.get(area_key)
    r3d = get_region_3d(context)
    
    if active == 'FRONT':
        try:
            bpy.ops.view3d.viewnumpad(type='BACK')
            if r3d:
                r3d.view_perspective = 'ORTHO'
            _area_active_view[area_key] = 'BACK'
        except:
            pass
    elif active == 'BACK':
        try:
            bpy.ops.view3d.viewnumpad(type='FRONT')
            if r3d:
                r3d.view_perspective = 'ORTHO'
            _area_active_view[area_key] = 'FRONT'
        except:
            pass
    else:
        try:
            bpy.ops.view3d.viewnumpad(type='FRONT')
            if r3d:
                r3d.view_perspective = 'ORTHO'
            _area_active_view[area_key] = 'FRONT'
        except:
            pass


def set_view(context, area_key, view_name):
    view_exec_map = {
        'FRONT': 'FRONT',
        'BACK': 'BACK',
        'LEFT': 'RIGHT',
        'RIGHT': 'LEFT',
        'TOP': 'BOTTOM',
        'BOTTOM': 'TOP'
    }
    
    try:
        bpy.ops.view3d.viewnumpad(type=view_exec_map.get(view_name, view_name))
        _area_active_view[area_key] = view_name
    except:
        pass


def set_home_view(context, area_key):
    try:
        bpy.ops.view3d.view_all(center=True)
        _area_active_view[area_key] = None
    except:
        pass


def activate_camera_view(context, area_key):
    if context.scene.camera and context.space_data and context.space_data.type == 'VIEW_3D':
        save_current_view(context, area_key)
        
        # Afecta únicamente al visor 3D actual donde se hizo clic
        context.space_data.region_3d.view_perspective = 'CAMERA'
        context.space_data.lock_camera = True
        
        _area_waiting_restore[area_key] = True
        _area_active_view[area_key] = 'CAM'


def deactivate_camera_view(context, area_key):
    restore_saved_view(context, area_key)
    _area_waiting_restore[area_key] = False
    _area_active_view[area_key] = None


def toggle_camera_view(context, area_key):
    r3d = get_region_3d(context)
    if r3d and r3d.view_perspective == 'CAMERA':
        deactivate_camera_view(context, area_key)
    else:
        if context.scene.camera:
            activate_camera_view(context, area_key)


def toggle_lock_camera(context):
    if context.space_data:
        context.space_data.lock_camera = not context.space_data.lock_camera


def undo_command(context, area_key):
    try:
        bpy.ops.ed.undo()
        _area_highlight[area_key] = 'UNDO'
    except:
        pass


def redo_command(context, area_key):
    try:
        bpy.ops.ed.redo()
        _area_highlight[area_key] = 'REDO'
    except:
        pass


def delete_command(context, area_key):
    try:
        bpy.ops.object.delete()
        _area_highlight[area_key] = 'DELETE'
    except:
        pass


# ----------------------------------------------------------
# FUNCIONES DE DIBUJO GEOMÉTRICO (BGL)
# ----------------------------------------------------------

def draw_rect(x, y, w, h):
    bgl.glBegin(bgl.GL_QUADS)
    bgl.glVertex2f(x, y)
    bgl.glVertex2f(x + w, y)
    bgl.glVertex2f(x + w, y + h)
    bgl.glVertex2f(x, y + h)
    bgl.glEnd()


def draw_rect_outline(x, y, w, h):
    bgl.glBegin(bgl.GL_LINE_LOOP)
    bgl.glVertex2f(x, y)
    bgl.glVertex2f(x + w, y)
    bgl.glVertex2f(x + w, y + h)
    bgl.glVertex2f(x, y + h)
    bgl.glEnd()


def draw_house_outline(x, y, w, h):
    """Casita con puerta delineada"""
    base_left = x + w * 0.20
    base_right = x + w * 0.80
    base_bottom = y + h * 0.15
    base_top = y + h * 0.55
    center_x = x + w / 2
    roof_top_y = y + h * 0.85
    
    bgl.glBegin(bgl.GL_LINE_LOOP)
    bgl.glVertex2f(base_left, base_bottom)
    bgl.glVertex2f(base_right, base_bottom)
    bgl.glVertex2f(base_right, base_top)
    bgl.glVertex2f(center_x, roof_top_y)
    bgl.glVertex2f(base_left, base_top)
    bgl.glEnd()
    
    door_w = w * 0.12
    door_h = h * 0.22
    door_x = x + (w * 0.5) - door_w / 2
    door_y = y + h * 0.15
    
    bgl.glBegin(bgl.GL_LINE_LOOP)
    bgl.glVertex2f(door_x, door_y)
    bgl.glVertex2f(door_x + door_w, door_y)
    bgl.glVertex2f(door_x + door_w, door_y + door_h)
    bgl.glVertex2f(door_x, door_y + door_h)
    bgl.glEnd()


def draw_triangle_outline(x1, y1, x2, y2, x3, y3):
    bgl.glBegin(bgl.GL_LINE_LOOP)
    bgl.glVertex2f(x1, y1)
    bgl.glVertex2f(x2, y2)
    bgl.glVertex2f(x3, y3)
    bgl.glEnd()


def draw_cross_outline(x, y, w, h):
    cx = x + w / 2
    cy = y + h / 2
    offset = w * 0.3
    
    bgl.glBegin(bgl.GL_LINES)
    bgl.glVertex2f(cx - offset, cy + offset)
    bgl.glVertex2f(cx + offset, cy - offset)
    bgl.glVertex2f(cx + offset, cy + offset)
    bgl.glVertex2f(cx - offset, cy - offset)
    bgl.glEnd()


def draw_arc_line_loop(cx, cy, radius, start_angle, end_angle, segments=16):
    bgl.glBegin(bgl.GL_LINE_STRIP)
    for i in range(segments + 1):
        angle = start_angle + (end_angle - start_angle) * i / segments
        x = cx + radius * math.cos(angle)
        y = cy + radius * math.sin(angle)
        bgl.glVertex2f(x, y)
    bgl.glEnd()


def draw_lock_icon(x, y, w, h, active):
    cx = x + w / 2
    cy = y + h / 2
    
    body_w = w * 0.25 + 5
    body_h = h * 0.18 + 5
    arch_radius = w * 0.12
    
    if active:
        bgl.glColor4f(1.0, 0.5, 0.0, 1.0)
    else:
        bgl.glColor4f(1.0, 1.0, 1.0, 1.0)
    
    body_left = cx - body_w / 2
    body_right = cx + body_w / 2
    body_bottom = cy - body_h / 2
    body_top = cy + body_h / 2
    
    bgl.glBegin(bgl.GL_QUADS)
    bgl.glVertex2f(body_left, body_bottom)
    bgl.glVertex2f(body_right, body_bottom)
    bgl.glVertex2f(body_right, body_top)
    bgl.glVertex2f(body_left, body_top)
    bgl.glEnd()
    
    arch_center_y = cy + body_h / 2
    arch_cx = cx
    arch_cy = arch_center_y + arch_radius
    
    draw_arc_line_loop(arch_cx, arch_cy, arch_radius, math.pi, 0, 16)
    
    bgl.glBegin(bgl.GL_LINES)
    bgl.glVertex2f(arch_cx - arch_radius, arch_cy)
    bgl.glVertex2f(arch_cx - arch_radius, arch_center_y)
    bgl.glVertex2f(arch_cx + arch_radius, arch_cy)
    bgl.glVertex2f(arch_cx + arch_radius, arch_center_y)
    bgl.glEnd()


def draw_camera_icon(x, y, w, h, active):
    cx = x + w / 2
    cy = y + h / 2
    
    body_w = w * 0.45
    body_h = h * 0.35
    triangle_w = w * 0.30
    
    body_left = cx - body_w / 2
    body_right = cx + body_w / 2
    body_bottom = cy - body_h / 2
    body_top = cy + body_h / 2
    
    tip_x = body_right
    tip_y = cy
    
    base_x = body_right + triangle_w
    base_y_bottom = body_bottom
    base_y_top = body_top
    
    if active:
        bgl.glColor4f(1.0, 0.5, 0.0, 1.0)
    else:
        bgl.glColor4f(1.0, 1.0, 1.0, 1.0)
    
    bgl.glBegin(bgl.GL_QUADS)
    bgl.glVertex2f(body_left, body_bottom)
    bgl.glVertex2f(body_right, body_bottom)
    bgl.glVertex2f(body_right, body_top)
    bgl.glVertex2f(body_left, body_top)
    bgl.glEnd()
    
    bgl.glBegin(bgl.GL_TRIANGLES)
    bgl.glVertex2f(tip_x, tip_y)
    bgl.glVertex2f(base_x, base_y_bottom)
    bgl.glVertex2f(base_x, base_y_top)
    bgl.glEnd()


def draw_triangle(x1, y1, x2, y2, x3, y3):
    bgl.glBegin(bgl.GL_TRIANGLES)
    bgl.glVertex2f(x1, y1)
    bgl.glVertex2f(x2, y2)
    bgl.glVertex2f(x3, y3)
    bgl.glEnd()


def draw_arrow_up(rect):
    x, y, w, h = rect
    cx = x + w * 0.5
    draw_triangle(cx, y + h - 6, x + 6, y + 6, x + w - 6, y + 6)


def draw_arrow_down(rect):
    x, y, w, h = rect
    cx = x + w * 0.5
    draw_triangle(cx, y + 6, x + 6, y + h - 6, x + w - 6, y + h - 6)


def draw_arrow_left(rect):
    x, y, w, h = rect
    cy = y + h * 0.5
    draw_triangle(x + 6, cy, x + w - 6, y + h - 6, x + w - 6, y + 6)


def draw_arrow_right(rect):
    x, y, w, h = rect
    cy = y + h * 0.5
    draw_triangle(x + w - 6, cy, x + 6, y + h - 6, x + 6, y + 6)


def draw_square(rect):
    x, y, w, h = rect
    m = 8
    bgl.glBegin(bgl.GL_QUADS)
    bgl.glVertex2f(x + m, y + m)
    bgl.glVertex2f(x + w - m, y + m)
    bgl.glVertex2f(x + w - m, y + h - m)
    bgl.glVertex2f(x + m, y + h - m)
    bgl.glEnd()


def draw_triangle_left_outline(rect, highlighted=False):
    x, y, w, h = rect
    cy = y + h * 0.5
    if highlighted:
        bgl.glColor4f(0.0, 1.0, 0.0, 1.0)
    else:
        bgl.glColor4f(0.0, 0.8, 0.0, 1.0)
    draw_triangle_outline(x + 6, cy, x + w - 6, y + h - 6, x + w - 6, y + 6)


def draw_triangle_right_outline(rect, highlighted=False):
    x, y, w, h = rect
    cy = y + h * 0.5
    if highlighted:
        bgl.glColor4f(0.0, 1.0, 0.0, 1.0)
    else:
        bgl.glColor4f(0.0, 0.8, 0.0, 1.0)
    draw_triangle_outline(x + w - 6, cy, x + 6, y + h - 6, x + 6, y + 6)


def get_button_color(view_type, active_view):
    if view_type == active_view:
        return (1.0, 0.5, 0.0)
    return (1.0, 1.0, 1.0)


def point_in_rect(mx, my, rect):
    if rect is None:
        return False
    x, y, w, h = rect
    return (mx >= x and mx <= x + w and my >= y and my <= y + h)


# ----------------------------------------------------------
# CALLBACK PRINCIPAL DE DIBUJO (VIEWPORT 3D)
# ----------------------------------------------------------

def draw_callback():
    context = bpy.context
    if not context.area or context.area.type != 'VIEW_3D':
        return
    if not context.scene.view_alfa_enabled:
        return
    
    region = context.region
    area_key = context.area.as_pointer()
    
    # --- AUTO-INICIO (igual que kit_tools_pro) ---
    if area_key not in _modal_areas:
        _modal_areas[area_key] = True
        try:
            bpy.ops.view_alfa.modal('INVOKE_DEFAULT')
        except Exception as e:
            _modal_areas.pop(area_key, None)
            print("Quick Views - draw_callback arranque fallo:", e)
    
    # --- CALCULAR RECTS POR ÁREA ---
    size = 34
    spacing = 5
    
    ox = region.width - 130
    oy = region.height - 150
    
    front_center_x = ox + size + spacing + (size / 2)
    right_button_x = ox + (size + spacing) * 2
    
    block_width = size * 3 + spacing * 2
    block_start_x = front_center_x - (block_width / 2)
    undo_y = oy + size * 2 + spacing + size + spacing
    
    rect_undo = (block_start_x, undo_y, size, size)
    rect_delete = (block_start_x + size + spacing, undo_y, size, size)
    rect_redo = (block_start_x + (size + spacing) * 2, undo_y, size, size)
    
    rect_top = (ox + size + spacing, oy + size * 2 + spacing - 34, size, size)
    rect_left = (ox, oy + size + spacing - 34, size, size)
    rect_front = (ox + size + spacing, oy + size + spacing - 34, size, size)
    rect_right = (ox + (size + spacing) * 2, oy + size + spacing - 34, size, size)
    rect_bottom = (ox + size + spacing, oy + spacing - 34, size, size)
    
    right_col_x = right_button_x
    col_y_start = oy + spacing - 34 - spacing - size
    
    rect_cam = (right_col_x, col_y_start, size, size)
    rect_lock = (right_col_x, col_y_start - size - spacing, size, size)
    rect_home = (right_col_x, col_y_start - (size + spacing) * 2, size, size)
    
    # Guardar en _area_rects
    _area_rects[area_key] = {
        'undo': rect_undo,
        'delete': rect_delete,
        'redo': rect_redo,
        'top': rect_top,
        'left': rect_left,
        'front': rect_front,
        'right': rect_right,
        'bottom': rect_bottom,
        'cam': rect_cam,
        'lock': rect_lock,
        'home': rect_home
    }
    
    # --- OBTENER ESTADOS DEL ÁREA ---
    active_view = _area_active_view.get(area_key)
    highlight = _area_highlight.get(area_key)
    highlight_timer = _area_highlight_timer.get(area_key, 0)
    lock_active = context.space_data.lock_camera if context.space_data else False
    
    # --- DIBUJAR ---
    bgl.glEnable(bgl.GL_BLEND)
    
    # Fondos ViewCube
    bgl.glColor4f(0.15, 0.15, 0.15, 0.85)
    for r in (rect_top, rect_left, rect_front, rect_right, rect_bottom):
        draw_rect(*r)
    
    # Bordes complementarios
    bgl.glColor4f(0.15, 0.15, 0.15, 0.85)
    draw_rect_outline(*rect_undo)
    draw_rect_outline(*rect_delete)
    draw_rect_outline(*rect_redo)
    draw_rect_outline(*rect_cam)
    draw_rect_outline(*rect_lock)
    draw_rect_outline(*rect_home)
    
    # Dibujo de herramientas
    draw_triangle_left_outline(rect_undo, highlight == 'UNDO')
    draw_triangle_right_outline(rect_redo, highlight == 'REDO')
    
    if highlight == 'DELETE':
        bgl.glColor4f(1.0, 0.3, 0.3, 1.0)
    else:
        bgl.glColor4f(1.0, 0.0, 0.0, 1.0)
    draw_cross_outline(*rect_delete)
    
    # Flechas de dirección
    color = get_button_color('TOP', active_view)
    bgl.glColor4f(color[0], color[1], color[2], 1.0)
    draw_arrow_up(rect_top)
    
    color = get_button_color('LEFT', active_view)
    bgl.glColor4f(color[0], color[1], color[2], 1.0)
    draw_arrow_left(rect_left)
    
    if active_view in ('FRONT', 'BACK'):
        bgl.glColor4f(1.0, 0.5, 0.0, 1.0)
    else:
        bgl.glColor4f(1.0, 1.0, 1.0, 1.0)
    draw_square(rect_front)
    
    color = get_button_color('RIGHT', active_view)
    bgl.glColor4f(color[0], color[1], color[2], 1.0)
    draw_arrow_right(rect_right)
    
    color = get_button_color('BOTTOM', active_view)
    bgl.glColor4f(color[0], color[1], color[2], 1.0)
    draw_arrow_down(rect_bottom)
    
    # Íconos funcionales
    draw_camera_icon(*rect_cam, active_view == 'CAM')
    draw_lock_icon(*rect_lock, lock_active)
    
    bgl.glColor4f(1.0, 0.8, 0.0, 1.0)
    draw_house_outline(*rect_home)
    
    bgl.glDisable(bgl.GL_BLEND)
    
    # --- MANEJAR HIGHLIGHT TIMER ---
    if highlight:
        _area_highlight_timer[area_key] = highlight_timer + 1
        if _area_highlight_timer[area_key] > 10:
            _area_highlight.pop(area_key, None)
            _area_highlight_timer.pop(area_key, None)


# ----------------------------------------------------------
# OPERADOR MODAL (Multi-vista)
# ----------------------------------------------------------

class VIEW_ALFA_OT_modal(bpy.types.Operator):
    bl_idname = "view_alfa.modal"
    bl_label = "View Alfa Modal"
    
    _area_key = None
    
    def modal(self, context, event):
        if not context.scene.view_alfa_enabled:
            return {'PASS_THROUGH'}
        
        # Verificar si el área sigue viva
        alive = any(
            area.as_pointer() == self._area_key
            for window in bpy.context.window_manager.windows
            for area in window.screen.areas
            if area.type == 'VIEW_3D'
        )
        if not alive:
            _modal_areas.pop(self._area_key, None)
            _area_rects.pop(self._area_key, None)
            _area_active_view.pop(self._area_key, None)
            _area_saved_data.pop(self._area_key, None)
            _area_waiting_restore.pop(self._area_key, None)
            _area_highlight.pop(self._area_key, None)
            _area_highlight_timer.pop(self._area_key, None)
            print("Quick Views: area cerrada, modal terminado ({})".format(self._area_key))
            return {'CANCELLED'}
        
        # Redibujar constantemente
        if context.area:
            context.area.tag_redraw()
        
        # Control de navegación tradicional
        if event.type == 'MIDDLEMOUSE' or (event.type == 'MOUSEMOVE' and event.value == 'PRESS'):
            active = _area_active_view.get(self._area_key)
            if active not in ('CAM', 'ORTHO'):
                _area_active_view[self._area_key] = None
        
        # Procesamiento de clics
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            mx = event.mouse_region_x
            my = event.mouse_region_y
            rects = _area_rects.get(self._area_key)
            
            if rects:
                if point_in_rect(mx, my, rects.get('undo')):
                    undo_command(context, self._area_key)
                    return {'RUNNING_MODAL'}
                if point_in_rect(mx, my, rects.get('delete')):
                    delete_command(context, self._area_key)
                    return {'RUNNING_MODAL'}
                if point_in_rect(mx, my, rects.get('redo')):
                    redo_command(context, self._area_key)
                    return {'RUNNING_MODAL'}
                if point_in_rect(mx, my, rects.get('cam')):
                    toggle_camera_view(context, self._area_key)
                    return {'RUNNING_MODAL'}
                if point_in_rect(mx, my, rects.get('lock')):
                    toggle_lock_camera(context)
                    return {'RUNNING_MODAL'}
                if point_in_rect(mx, my, rects.get('front')):
                    toggle_front_back_view(context, self._area_key)
                    return {'RUNNING_MODAL'}
                if point_in_rect(mx, my, rects.get('left')):
                    set_view(context, self._area_key, 'LEFT')
                    return {'RUNNING_MODAL'}
                if point_in_rect(mx, my, rects.get('right')):
                    set_view(context, self._area_key, 'RIGHT')
                    return {'RUNNING_MODAL'}
                if point_in_rect(mx, my, rects.get('top')):
                    set_view(context, self._area_key, 'TOP')
                    return {'RUNNING_MODAL'}
                if point_in_rect(mx, my, rects.get('bottom')):
                    set_view(context, self._area_key, 'BOTTOM')
                    return {'RUNNING_MODAL'}
                if point_in_rect(mx, my, rects.get('home')):
                    set_home_view(context, self._area_key)
                    return {'RUNNING_MODAL'}
        
        return {'PASS_THROUGH'}
    
    def invoke(self, context, event):
        self._area_key = context.area.as_pointer()
        context.window_manager.modal_handler_add(self)
        print("Quick Views: modal activo en area {}".format(self._area_key))
        return {'RUNNING_MODAL'}


# ----------------------------------------------------------
# OPERADOR DE REINICIO MANUAL
# ----------------------------------------------------------

class VIEW_ALFA_OT_start(bpy.types.Operator):
    bl_idname = "view_alfa.start"
    bl_label = "Fix / Restart Quick Views"
    
    def execute(self, context):
        # Limpiar todo
        _modal_areas.clear()
        _area_rects.clear()
        _area_active_view.clear()
        _area_saved_data.clear()
        _area_waiting_restore.clear()
        _area_highlight.clear()
        _area_highlight_timer.clear()
        
        # Forzar redibujado
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == 'VIEW_3D':
                    area.tag_redraw()
        return {'FINISHED'}


# ----------------------------------------------------------
# HEADER
# ----------------------------------------------------------

class VIEW_ALFA_OT_header_start(bpy.types.Operator):
    bl_idname = "view_alfa.header_start"
    bl_label = "Quick Views"
    bl_description = "Iniciar el Quick Views en el viewport 3D"
    
    def execute(self, context):
        # Forzar redibujado para que draw_callback lance los modales
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == 'VIEW_3D':
                    area.tag_redraw()
        return {'FINISHED'}


class VIEW_ALFA_HT_header_fixed(bpy.types.Header):
    bl_space_type = 'VIEW_3D'
    
    def draw(self, context):
        layout = self.layout
        if getattr(context.scene, "view_alfa_enabled", False):
            layout.operator("view_alfa.header_start", text="", icon='VIEW3D')


# ----------------------------------------------------------
# PANEL
# ----------------------------------------------------------

class VIEW_ALFA_PT_panel(bpy.types.Panel):
    bl_label = "Quick Views"
    bl_idname = "VIEW_ALFA_PT_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Quick Views"
    
    def draw(self, context):
        layout = self.layout
        layout.prop(context.scene, "view_alfa_enabled", text="Enable Quick Views")
        layout.operator("view_alfa.start", text="Fix / Restart UI", icon='REFRESH')
        layout.label("Vistas activas: {}".format(len(_modal_areas)))


# ----------------------------------------------------------
# HANDLER - solo para limpiar estado al cargar .blend
# ----------------------------------------------------------

@persistent
def auto_start_handler(dummy):
    """Al cargar un .blend: limpiar registros.
    draw_callback se encargará de lanzar los modales
    en cuanto cada área se redibuje."""
    _modal_areas.clear()
    _area_rects.clear()
    _area_active_view.clear()
    _area_saved_data.clear()
    _area_waiting_restore.clear()
    _area_highlight.clear()
    _area_highlight_timer.clear()
    print("Quick Views: estado limpiado, esperando redibujado de areas.")


# ----------------------------------------------------------
# REGISTRO
# ----------------------------------------------------------

def register():
    global _draw_handle
    
    bpy.utils.register_class(VIEW_ALFA_OT_modal)
    bpy.utils.register_class(VIEW_ALFA_OT_start)
    bpy.utils.register_class(VIEW_ALFA_OT_header_start)
    bpy.utils.register_class(VIEW_ALFA_HT_header_fixed)
    bpy.utils.register_class(VIEW_ALFA_PT_panel)
    
    bpy.types.Scene.view_alfa_enabled = bpy.props.BoolProperty(
        name="Enable Quick Views",
        default=True
    )
    
    # Un solo draw_handler global
    _draw_handle = bpy.types.SpaceView3D.draw_handler_add(
        draw_callback, (), 'WINDOW', 'POST_PIXEL'
    )
    
    # Limpiar diccionarios
    _modal_areas.clear()
    _area_rects.clear()
    _area_active_view.clear()
    _area_saved_data.clear()
    _area_waiting_restore.clear()
    _area_highlight.clear()
    _area_highlight_timer.clear()
    
    # Handler de carga
    bpy.app.handlers.load_post.append(auto_start_handler)


def unregister():
    global _draw_handle
    
    if auto_start_handler in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(auto_start_handler)
    
    if _draw_handle:
        bpy.types.SpaceView3D.draw_handler_remove(_draw_handle, 'WINDOW')
        _draw_handle = None
    
    # Limpiar diccionarios
    _modal_areas.clear()
    _area_rects.clear()
    _area_active_view.clear()
    _area_saved_data.clear()
    _area_waiting_restore.clear()
    _area_highlight.clear()
    _area_highlight_timer.clear()
    
    if hasattr(bpy.types.Scene, "view_alfa_enabled"):
        del bpy.types.Scene.view_alfa_enabled
    
    bpy.utils.unregister_class(VIEW_ALFA_OT_modal)
    bpy.utils.unregister_class(VIEW_ALFA_OT_start)
    bpy.utils.unregister_class(VIEW_ALFA_OT_header_start)
    bpy.utils.unregister_class(VIEW_ALFA_HT_header_fixed)
    bpy.utils.unregister_class(VIEW_ALFA_PT_panel)


if __name__ == "__main__":
    register()