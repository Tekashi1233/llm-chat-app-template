using System.Windows;
using Athena.Core;

namespace Athena;

/// <summary>
/// ATHENA — native Windows shell (N1). Entry point only.
/// N8: there is no StartupUri — OnStartup builds the MainWindow
/// so the --tray argument can start it hidden (autostart after
/// login). A normal launch shows the window as before.
/// </summary>
public partial class App : Application
{
    public App()
    {
        DispatcherUnhandledException += (_, e) =>
        {
            var inner = e.Exception.InnerException?.Message;
            var detail = string.IsNullOrWhiteSpace(inner)
                ? e.Exception.Message
                : $"{e.Exception.Message}\n{inner}";
            MessageBox.Show(
                $"Something went wrong:\n{detail}",
                "ATHENA",
                MessageBoxButton.OK,
                MessageBoxImage.Error);
            e.Handled = true;
        };
    }

    protected override void OnStartup(StartupEventArgs e)
    {
        base.OnStartup(e);
        var options = StartupOptions.Parse(e.Args);
        var window = new MainWindow(options);
        MainWindow = window;
        if (!options.StartHidden)
            window.Show();
    }
}
