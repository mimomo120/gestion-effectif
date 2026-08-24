from django import forms

class RegisterForm(forms.Form):
    it = forms.CharField(max_length=100)
    password = forms.CharField(min_length=8, widget=forms.PasswordInput)

    def clean_it(self):
        return self.cleaned_data['it'].strip()