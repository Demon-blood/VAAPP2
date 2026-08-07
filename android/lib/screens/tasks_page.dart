import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import 'package:provider/provider.dart';

import '../app_state.dart';
import '../widgets/common_widgets.dart';

class TasksPage extends StatelessWidget {
  const TasksPage({super.key});

  @override
  Widget build(BuildContext context) {
    final tasks = context.watch<AppState>().tasks;
    if (tasks.isEmpty) {
      return const EmptyState(
        icon: Icons.task_alt,
        title: 'Nothing waiting',
        message: 'Tasks appear only when a live email, deadline, payment, or workflow creates one.',
      );
    }
    return ListView.separated(
      padding: const EdgeInsets.all(8),
      itemCount: tasks.length,
      separatorBuilder: (_, _) => const Divider(height: 1),
      itemBuilder: (context, index) {
        final task = tasks[index];
        final due = DateTime.tryParse('${task['due_at'] ?? ''}');
        final completed = task['status'] == 'completed';
        return CheckboxListTile(
          value: completed,
          onChanged: (checked) => context.read<AppState>().setTaskStatus(
                task['id'] as int,
                checked == true ? 'completed' : 'open',
              ),
          title: Text('${task['title']}'),
          subtitle: Text([
            if ('${task['description'] ?? ''}'.isNotEmpty) '${task['description']}',
            if (due != null) 'Due ${DateFormat('dd MMM yyyy HH:mm').format(due)}',
            if (task['requires_approval'] == true) 'Approval required',
          ].join('\n')),
          controlAffinity: ListTileControlAffinity.leading,
        );
      },
    );
  }
}
