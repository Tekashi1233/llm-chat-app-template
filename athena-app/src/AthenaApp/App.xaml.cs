using System.Windows;

namespace Athena;

/// <summary>ATHENA — native Windows shell (N1). Entry point only.</summary>
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
}
