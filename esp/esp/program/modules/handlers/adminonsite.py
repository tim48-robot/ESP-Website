
__author__    = "Individual contributors (see AUTHORS file)"
__date__      = "$DATE$"
__rev__       = "$REV$"
__license__   = "AGPL v.3"
__copyright__ = """
This file is part of the ESP Web Site
Copyright (c) 2024 by the individual contributors
  (see AUTHORS file)

The ESP Web Site is free software; you can redistribute it and/or
modify it under the terms of the GNU Affero General Public License
as published by the Free Software Foundation; either version 3
of the License, or (at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU Affero General Public License for more details.

You should have received a copy of the GNU Affero General Public
License along with this program; if not, write to the Free Software
Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.

Contact information:
MIT Educational Studies Program
  84 Massachusetts Ave W20-467, Cambridge, MA 02139
  Phone: 617-253-4882
  Email: esp-webmasters@mit.edu
Learning Unlimited, Inc.
  527 Franklin St, Cambridge, MA 02139
  Phone: 617-379-0178
  Email: web-team@learningu.org 
"""

import json
import logging
from datetime import datetime # Import essential django utilities 

from django.db import transaction
from django.db.models import Count, Sum, F
from django.shortcuts import render_to_response, get_object_or_404, redirect
from django.template import RequestContext
from django.http import HttpResponse, HttpResponseRedirect, Http404, JsonResponse

from esp.program.models import ClassSubject, ClassSection, StudentRegistration
from esp.program.models.class_ import OPEN, CLOSED
from esp.program.modules.base import ProgramModuleObj, needs_admin, main_call, aux_call
from esp.users.models import ESPUser, Record, RecordType
from esp.tagdict.models import Tag
from esp.utils.web import render_to_response

logger = logging.getLogger(__name__)


class AdminOnsite(ProgramModuleObj):
    doc = """Provides a mobile-friendly admin dashboard for onsite event management.
    Includes live class enrollment monitoring, per-class registration toggles,
    cap modifications, and quick stats about check-ins and attendance."""

    @classmethod
    def module_properties(cls):
        return {
            "admin_title": "Admin Onsite Webapp",
            "link_title": "Admin Onsite Dashboard",
            "module_type": "onsite",
            "seq": 9000,
            "choosable": 1,
        }

    # ──────────────────────────────────────────────
    #  Helper: build shared context for all views
    # ──────────────────────────────────────────────
    def _base_context(self, request, prog):
        one = prog.url.split('/')[0]
        two = '/'.join(prog.url.split('/')[1:])
        return {
            'program': prog,
            'one': one,
            'two': two,
            'user': request.user,
            'module_base_url': f"/{self.module_properties()['module_type']}/{one}/{two}",
        }

    # ──────────────────────────────────────────────
    #  Main Dashboard View
    # ──────────────────────────────────────────────
    @main_call
    @needs_admin
    def adminonsite(self, request, tl, one, two, module, extra, prog):
        """Display the admin onsite dashboard with quick stats."""
        context = self._base_context(request, prog)
        context['webapp_page'] = 'dashboard'
        context['stats'] = self._get_dashboard_stats(prog)
        context['missing_teachers'] = self._get_missing_teachers(prog)
        return render_to_response(self.baseDir() + 'dashboard.html', request, context)

    # ──────────────────────────────────────────────
    #  Classes List View
    # ──────────────────────────────────────────────
    @aux_call
    @needs_admin
    def adminonsite_classes(self, request, tl, one, two, module, extra, prog):
        """Display all class sections with enrollment progress bars."""
        context = self._base_context(request, prog)
        context['webapp_page'] = 'classes'
        context['sections'] = self._get_sections_data(prog)
        context['timeslots'] = prog.getTimeSlots().order_by('start')
        return render_to_response(self.baseDir() + 'classes.html', request, context)

    # ──────────────────────────────────────────────
    #  Class Detail View (mini manage page)
    # ──────────────────────────────────────────────
    @aux_call
    @needs_admin
    def adminonsite_classdetail(self, request, tl, one, two, module, extra, prog):
        """Display a mini class management page for a single section."""
        try:
            section = ClassSection.objects.get(id=extra, parent_class__parent_program=prog)
        except (ClassSection.DoesNotExist, ValueError):
            return HttpResponse('Section not found', status=404)

        context = self._base_context(request, prog)
        context['webapp_page'] = 'classes'
        context['section'] = section
        context['cls'] = section.parent_class
        context['enrolled_students'] = section.students()
        context['num_enrolled'] = section.num_students()
        context['capacity'] = section.capacity
        context['is_open'] = section.isRegOpen()
        context['raw_capacity'] = section.max_class_capacity if section.max_class_capacity is not None else section.capacity
        context['teachers'] = section.parent_class.get_teachers()
        context['meeting_times'] = section.meeting_times.all().order_by('start')
        context['rooms'] = section.classrooms()
        return render_to_response(self.baseDir() + 'classdetail.html', request, context)

    # ──────────────────────────────────────────────
    #  Settings View
    # ──────────────────────────────────────────────
    @aux_call
    @needs_admin
    def adminonsite_settings(self, request, tl, one, two, module, extra, prog):
        """Display onsite configuration settings."""
        context = self._base_context(request, prog)
        context['webapp_page'] = 'settings'

        if request.method == 'POST':
            # Save settings as program tags
            refresh_interval = request.POST.get('refresh_interval', '10')
            use_checkin = request.POST.get('use_checkin', 'false')
            overenrollment = request.POST.get('overenrollment', 'false')

            Tag.setTag('adminonsite_refresh_interval', target=prog, value=refresh_interval)
            Tag.setTag('adminonsite_use_checkin', target=prog, value=use_checkin)
            Tag.setTag('adminonsite_overenrollment', target=prog, value=overenrollment)
            context['saved'] = True

        context['refresh_interval'] = Tag.getProgramTag('adminonsite_refresh_interval', program=prog, default='10')
        context['use_checkin'] = Tag.getProgramTag('adminonsite_use_checkin', program=prog, default='false')
        context['overenrollment'] = Tag.getProgramTag('adminonsite_overenrollment', program=prog, default='false')
        return render_to_response(self.baseDir() + 'settings.html', request, context)

    # ──────────────────────────────────────────────
    #  Teacher Check-in View
    # ──────────────────────────────────────────────
    @aux_call
    @needs_admin
    def adminonsite_teachercheckin(self, request, tl, one, two, module, extra, prog):
        """Display a mobile-native teacher checkin UI."""
        context = self._base_context(request, prog)
        context['webapp_page'] = 'dashboard'
        
        # List teachers for this program
        teachers = ESPUser.objects.filter(
            classsubject__parent_program=prog,
            classsubject__status__gte=10
        ).distinct().order_by('last_name', 'first_name')

        now = datetime.now()
        checked_in_ids = set(Record.objects.filter(
            program=prog,
            event__name='teacher_checked_in',
            time__year=now.year,
            time__month=now.month,
            time__day=now.day,
        ).values_list('user_id', flat=True))

        context['teachers'] = teachers
        context['checked_in_ids'] = checked_in_ids
        return render_to_response(self.baseDir() + 'teachercheckin.html', request, context)

    # ──────────────────────────────────────────────
    #  Teacher Check-in POST (AJAX)
    # ──────────────────────────────────────────────
    @aux_call
    @needs_admin
    def adminonsite_do_teacher_checkin(self, request, tl, one, two, module, extra, prog):
        """Record a teacher check-in for today."""
        if request.method != 'POST':
            return JsonResponse({'error': 'POST required'}, status=405)

        try:
            teacher_id = int(request.POST.get('teacher_id', 0))
        except (ValueError, TypeError):
            return JsonResponse({'error': 'Invalid teacher_id'}, status=400)

        try:
            teacher = ESPUser.objects.get(pk=teacher_id)
        except ESPUser.DoesNotExist:
            return JsonResponse({'error': 'Teacher not found'}, status=404)

        now = datetime.now()
        already = Record.objects.filter(
            program=prog,
            event__name='teacher_checked_in',
            user=teacher,
            time__year=now.year,
            time__month=now.month,
            time__day=now.day,
        ).exists()

        if not already:
            rt = RecordType.objects.get(name='teacher_checked_in')
            Record.objects.create(user=teacher, event=rt, program=prog, time=now)

        return JsonResponse({'ok': True, 'already': already, 'teacher': teacher.name()})

    # ──────────────────────────────────────────────
    #  Student Check-in View
    # ──────────────────────────────────────────────
    @aux_call
    @needs_admin
    def adminonsite_studentcheckin_search(self, request, tl, one, two, module, extra, prog):
        """Display a mobile-native student checkin UI."""
        context = self._base_context(request, prog)
        context['webapp_page'] = 'dashboard'
        return render_to_response(self.baseDir() + 'studentcheckin_search.html', request, context)

    # ──────────────────────────────────────────────
    #  Student Search AJAX (GET)
    # ──────────────────────────────────────────────
    @aux_call
    @needs_admin
    def adminonsite_student_search(self, request, tl, one, two, module, extra, prog):
        """Search registered students by name or username and return JSON."""
        q = request.GET.get('q', '').strip()
        if len(q) < 2:
            return JsonResponse({'students': []})

        from esp.program.models import RegistrationType
        try:
            enrolled_type = RegistrationType.get_map().get('Enrolled', None)
            if enrolled_type:
                base_qs = ESPUser.objects.filter(
                    studentregistration__section__parent_class__parent_program=prog,
                    studentregistration__relationship=enrolled_type,
                ).distinct()
            else:
                base_qs = ESPUser.objects.filter(
                    classsubject__parent_program=prog
                ).distinct()
        except Exception:
            base_qs = ESPUser.objects.none()

        from django.db.models import Q
        parts = q.split()
        if len(parts) >= 2:
            qs = base_qs.filter(
                Q(first_name__icontains=parts[0], last_name__icontains=parts[1]) |
                Q(first_name__icontains=parts[1], last_name__icontains=parts[0]) |
                Q(username__icontains=q)
            )
        else:
            qs = base_qs.filter(
                Q(first_name__icontains=q) |
                Q(last_name__icontains=q) |
                Q(username__icontains=q)
            )

        results = []
        for student in qs[:10]:
            results.append({
                'id': student.id,
                'name': student.name(),
                'username': student.username,
                'checked_in': prog.isCheckedIn(student),
            })
        return JsonResponse({'students': results})

    # ──────────────────────────────────────────────
    #  Student Check-in POST (AJAX)
    # ──────────────────────────────────────────────
    @aux_call
    @needs_admin
    def adminonsite_do_student_checkin(self, request, tl, one, two, module, extra, prog):
        """Record a student attended check-in."""
        if request.method != 'POST':
            return JsonResponse({'error': 'POST required'}, status=405)

        try:
            student_id = int(request.POST.get('student_id', 0))
        except (ValueError, TypeError):
            return JsonResponse({'error': 'Invalid student_id'}, status=400)

        try:
            student = ESPUser.objects.get(pk=student_id)
        except ESPUser.DoesNotExist:
            return JsonResponse({'error': 'Student not found'}, status=404)

        already = prog.isCheckedIn(student)
        if not already:
            rt = RecordType.objects.get(name='attended')
            Record.objects.create(user=student, event=rt, program=prog)

        return JsonResponse({'ok': True, 'already': already, 'student': student.name()})

    # ──────────────────────────────────────────────
    #  JSON Data Endpoint (for AJAX polling)
    # ──────────────────────────────────────────────
    @aux_call
    @needs_admin
    def adminonsite_data(self, request, tl, one, two, module, extra, prog):
        """Return all dashboard data as JSON for AJAX polling."""
        data = {
            'stats': self._get_dashboard_stats(prog),
            'sections': self._get_sections_data(prog),
            'timestamp': datetime.now().isoformat(),
        }
        return JsonResponse(data)

    # ──────────────────────────────────────────────
    #  Toggle Registration (POST)
    # ──────────────────────────────────────────────
    @aux_call
    @needs_admin
    def adminonsite_toggle_reg(self, request, tl, one, two, module, extra, prog):
        """Toggle registration open/closed for a section."""
        if request.method != 'POST':
            return JsonResponse({'error': 'POST required'}, status=405)

        try:
            section_id = int(request.POST.get('section_id', 0))
            action = request.POST.get('action', '')  # 'open' or 'close'
        except (ValueError, TypeError):
            return JsonResponse({'error': 'Invalid parameters'}, status=400)

        try:
            with transaction.atomic():
                section = ClassSection.objects.select_for_update().get(
                    id=section_id, parent_class__parent_program=prog
                )
                if action == 'open':
                    section.registration_status = OPEN
                elif action == 'close':
                    section.registration_status = CLOSED
                else:
                    return JsonResponse({'error': 'Invalid action'}, status=400)
                section.save()

            logger.info(
                "Admin %s toggled registration for section %s to %s",
                request.user.username, section_id, action
            )
            return JsonResponse({
                'success': True,
                'section_id': section_id,
                'registration_open': section.isRegOpen(),
            })
        except ClassSection.DoesNotExist:
            return JsonResponse({'error': 'Section not found'}, status=404)

    # ──────────────────────────────────────────────
    #  Update Cap (POST)
    # ──────────────────────────────────────────────
    @aux_call
    @needs_admin
    def adminonsite_update_cap(self, request, tl, one, two, module, extra, prog):
        """Update the capacity for a section, with optimistic locking."""
        if request.method != 'POST':
            return JsonResponse({'error': 'POST required'}, status=405)

        try:
            section_id = int(request.POST.get('section_id', 0))
            new_cap = int(request.POST.get('new_cap', 0))
        except (ValueError, TypeError):
            return JsonResponse({'error': 'Invalid parameters'}, status=400)

        if new_cap < 0:
            return JsonResponse({'error': 'Capacity must be non-negative'}, status=400)

        try:
            with transaction.atomic():
                section = ClassSection.objects.select_for_update().get(
                    id=section_id, parent_class__parent_program=prog
                )
                section.max_class_capacity = new_cap
                section.save()

            logger.info(
                "Admin %s updated cap for section %s to %d",
                request.user.username, section_id, new_cap
            )
            return JsonResponse({
                'success': True,
                'section_id': section_id,
                'new_cap': new_cap,
            })
        except ClassSection.DoesNotExist:
            return JsonResponse({'error': 'Section not found'}, status=404)

    # ──────────────────────────────────────────────
    #  Private Helpers
    # ──────────────────────────────────────────────
    def _get_dashboard_stats(self, prog):
        """Compute quick stats for the dashboard."""
        now = datetime.now()

        # Students checked in today
        students_checked_in = Record.objects.filter(
            program=prog,
            event__name='attended',
            time__year=now.year, time__month=now.month, time__day=now.day
        ).values('user').distinct().count()

        # Teachers checked in today
        teachers_checked_in = Record.objects.filter(
            program=prog,
            event__name='teacher_checked_in',
            time__year=now.year, time__month=now.month, time__day=now.day
        ).values('user').distinct().count()

        # Sections data
        sections = ClassSection.objects.filter(
            parent_class__parent_program=prog,
            status__gt=0
        )
        total_sections = sections.count()
        sections_open = sections.filter(registration_status=OPEN).count()
        sections_closed = sections.filter(registration_status=CLOSED).count()

        # Total enrolled (students with at least one enrollment)
        from esp.program.models import RegistrationType
        try:
            enrolled_type = RegistrationType.get_map().get('Enrolled', None)
            if enrolled_type:
                total_enrolled = StudentRegistration.valid_objects().filter(
                    section__parent_class__parent_program=prog,
                    relationship=enrolled_type
                ).values('user').distinct().count()
            else:
                total_enrolled = 0
        except Exception:
            total_enrolled = 0

        # Teachers total
        teachers_total = prog.teachers()
        teachers_total_count = 0
        for key, queryset in teachers_total.items():
            if 'class_approved' in key:
                teachers_total_count = queryset.count()
                break
        if teachers_total_count == 0:
            # Fallback: count all teachers who have at least one approved class
            teachers_total_count = ESPUser.objects.filter(
                classsubject__parent_program=prog,
                classsubject__status__gte=10
            ).distinct().count()

        return {
            'students_checked_in': students_checked_in,
            'teachers_checked_in': teachers_checked_in,
            'teachers_total': teachers_total_count,
            'missing_teachers': max(0, teachers_total_count - teachers_checked_in),
            'total_enrolled': total_enrolled,
            'sections_open': sections_open,
            'sections_closed': sections_closed,
            'total_sections': total_sections,
        }

    def _get_missing_teachers(self, prog):
        """Get a simplified list of missing teachers (not checked in today)."""
        now = datetime.now()
        checked_in_ids = set(Record.objects.filter(
            program=prog,
            event__name='teacher_checked_in',
            time__year=now.year, time__month=now.month, time__day=now.day
        ).values_list('user_id', flat=True))

        missing = []
        sections = ClassSection.objects.filter(
            parent_class__parent_program=prog,
            status__gt=0,
            meeting_times__start__date=now.date()
        ).select_related('parent_class').prefetch_related('parent_class__teachers').distinct()

        for section in sections[:20]:  # Limit for performance
            for teacher in section.parent_class.get_teachers():
                if teacher.id not in checked_in_ids:
                    missing.append({
                        'teacher_name': teacher.name(),
                        'class_title': section.parent_class.title,
                        'section_code': section.emailcode(),
                    })

        return missing

    def _get_sections_data(self, prog):
        """Get all active sections with enrollment and capacity info."""
        sections = ClassSection.objects.filter(
            parent_class__parent_program=prog,
            status__gt=0
        ).select_related('parent_class').prefetch_related(
            'meeting_times'
        ).order_by('meeting_times__start', 'parent_class__title')

        result = []
        seen = set()
        for section in sections:
            if section.id in seen:
                continue
            seen.add(section.id)

            times = section.meeting_times.all().order_by('start')
            time_str = ', '.join([t.short_description for t in times]) if times else 'TBD'

            rooms = section.classrooms()
            room_str = ', '.join([r.name for r in rooms]) if rooms else 'TBD'

            teachers = section.parent_class.get_teachers()
            teacher_names = [t.name() for t in teachers]

            enrolled = section.num_students()
            capacity = section.capacity

            result.append({
                'id': section.id,
                'title': section.parent_class.title,
                'code': section.emailcode(),
                'timeslot': time_str,
                'room': room_str,
                'enrolled': enrolled,
                'capacity': capacity if capacity else 0,
                'percent': round(100 * enrolled / float(capacity), 1) if capacity else 0,
                'registration_open': section.isRegOpen(),
                'status': section.status,
                'teachers': teacher_names,
            })

        return result

    def isStep(self):
        return False

    class Meta:
        proxy = True
        app_label = 'modules'
