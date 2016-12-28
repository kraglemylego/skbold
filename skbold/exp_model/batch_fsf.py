import os
import numpy as np
import os.path as op
from glob import glob
import nibabel as nib
import multiprocessing
import warnings
import skbold


class FsfCrawler(object):
    """
    Given an fsf-template, this crawler creates subject-specific fsf-FEAT files
    assuming that appropriate .bfsl files exist.

    Parameters
    ----------
    template : str
        Absolute path to template fsf-file. Default is 'mvpa', which models
        each bfsl-file as a separate regressor (and contrast against baseline).
    preproc_dir : str
        Absolute path to directory with preprocessed files.
    run_idf : str
        Identifier for run to apply template fsf to.
    output_dir : str
        Path to desired output dir of first-levels.
    subject_idf : str
        Identifier for subject-directories.
    func_idf : str
        Identifier for which functional should be use.
    prewhiten : bool
        Whether the data should be prewhitened in model fitting
    derivs : bool
        Whether to model derivatives of original regressors
    n_cores : int
        How many CPU cores should be used for the batch-analysis.
    """

    def __init__(self, preproc_dir, run_idf, template='mvpa',
                 output_dir=None, subject_idf='sub',
                 func_idf='func', prewhiten=True, derivs=False,
                 n_cores=1):

        self.template = template
        self.preproc_dir = preproc_dir

        if output_dir is None:
            output_dir = op.join(op.dirname(template), 'Firstlevels')

        if not op.isdir(output_dir):
            os.makedirs(output_dir)

        self.output_dir = output_dir
        self.run_idf = run_idf
        self.func_idf = func_idf
        self.subject_idf = subject_idf
        self.prewhiten = prewhiten
        self.derivs = derivs

        if n_cores == -1:
            n_cores = multiprocessing.cpu_count() - 1

        self.n_cores = n_cores
        self.clean_fsf = None
        self.out_fsf = []
        self.sub_dirs = None
        self.run_paths = None

    def crawl(self):
        """ Crawls subject-directories and spits out subject-specific fsf. """
        self._read_fsf()

        run_paths = op.join(self.preproc_dir, '*%s*' % self.subject_idf,
                            '*%s*' % self.run_idf)
        self.sub_dirs = sorted(glob(run_paths))

        fsf_paths = [self._write_fsf(sub) for sub in self.sub_dirs]

        shell_script = op.join(op.dirname(self.template), 'batch_fsf.sh')
        with open(shell_script, 'wb') as fout:

            for i, fsf in enumerate(fsf_paths):

                fout.write('feat %s &\n' % fsf)
                if (i+1) % self.n_cores == 0:
                    fout.write('wait\n')

    def _read_fsf(self):
        """ Reads in template-fsf and does some cleaning. """

        if self.template == 'single_trial':
            template = op.join(op.dirname(op.dirname(
                               op.abspath(__file__))), 'data',
                               'Feat_single_trial_template.fsf')

        else:
            template = self.template

        with open(template, 'rb') as f:
            template = f.readlines()

        template = [txt.replace('\n', '') for txt in template if txt != '\n']
        template = [txt for txt in template if txt[0] != '#']  # remove commnts

        self.clean_fsf = template

    def _write_fsf(self, sub_dir):
        """ Creates and writes out subject-specific fsf. """
        func_file = glob(op.join(sub_dir, '*%s*.nii.gz' % self.func_idf))

        if len(func_file) == 0:
            msg = "Found no func-file for sub %s" % sub_dir
            raise IOError(msg)
        elif len(func_file) > 1:
            msg = "Found more than one func-file for sub %s: %r" % (sub_dir,
                                                                    func_file)
            raise IOError(msg)
        else:
            func_file = func_file[0]

        out_dir = op.join(self.output_dir, op.basename(op.dirname(sub_dir)))
        if not op.isdir(out_dir):
            os.makedirs(out_dir)

        feat_dir = op.join(out_dir, '%s.feat' % self.run_idf)

        hdr = nib.load(func_file).header

        arg_dict = {'tr': hdr['pixdim'][4],
                    'npts': hdr['dim'][4],
                    'custom': sorted(glob(op.join(sub_dir, '*.bfsl'))),
                    'feat_files': "\"%s\"" % func_file,
                    'outputdir': "\"%s\"" % feat_dir,
                    'prewhiten_yn': str(int(self.prewhiten)),
                    'totalVoxels': np.prod(hdr['dim'][1:5])}

        if self.template == 'mvpa':
            arg_dict['ncon_orig'] = str(len(arg_dict['custom']))
            arg_dict['ncon_real'] = str(len(arg_dict['custom']))
            arg_dict['nftests_orig'] = '1'
            arg_dict['nftests_real'] = '1'
            arg_dict['evs_orig'] = str(len(arg_dict['custom']))
            arg_dict['evs_real'] = str(len(arg_dict['custom']))
            arg_dict.pop('custom')

        fsf_out = []
        # Loop over lines in cleaned template-fsf
        for line in self.clean_fsf:

            if any(key in line for key in arg_dict.keys()):
                parts = [txt for txt in line.split(' ') if txt]
                keys = [key for key in arg_dict.keys() if key in line][0]
                values = arg_dict[keys]

                if not isinstance(values, list):
                    parts[-1] = values
                elif len(values) > 1:
                    ev = line.split(os.sep)[-1].replace("\"", '')
                    bfsls = sorted(glob(op.join(sub_dir, '*.bfsl')))
                    to_set = [bfsl for bfsl in bfsls if ev in bfsl]

                    if len(to_set) == 1:
                        parts[-1] = "\"%s\"" % to_set[0]

                parts = [str(p) for p in parts]

                fsf_out.append(" ".join(parts))
            else:
                fsf_out.append(line)

        if self.template == 'mvpa':
            fsf_out = self._append_single_trial_info(
                sorted(glob(op.join(sub_dir, '*.bfsl'))), fsf_out)

        with open(op.join(sub_dir, 'design.fsf'), 'wb') as fsfout:
            print("Writing fsf to %s" % sub_dir)
            fsfout.write("\n".join(fsf_out))

        return op.join(sub_dir, 'design.fsf')

    def _append_single_trial_info(self, bfsls, fsf_out):
        """ Does some specific 'single-trial' (mvpa) stuff. """
        n_evs = len(bfsls)

        for i, bfsl in enumerate(bfsls):

            basename = op.basename(bfsl).split('.')[0]
            fsf_out.append('set fmri(evtitle%i) \"%s\"' % ((i + 1), basename))
            fsf_out.append('set fmri(shape%i) 3' % (i + 1))
            fsf_out.append('set fmri(convolve%i) 3' % (i + 1))
            fsf_out.append('set fmri(convolve_phase%i) 0' % (i + 1))
            fsf_out.append('set fmri(tempfilt_yn%i) 0' % (i + 1))
            fsf_out.append('set fmri(deriv_yn%i) %i' % ((i + 1), self.derivs))
            fsf_out.append('set fmri(custom%i) %s' % ((i + 1), bfsl))

            for x in range(n_evs + 1):
                fsf_out.append('set fmri(ortho%i.%i) 0' % ((i + 1), x))

            fsf_out.append('set fmri(conpic_real.%i) 0' % (i + 1))
            fsf_out.append('set fmri(conname_real.%i) \"%s\"' % ((i + 1), basename))
            fsf_out.append('set fmri(conpic_orig.%i) 0' % (i + 1))
            fsf_out.append('set fmri(conname_orig.%i) \"%s\"' % ((i + 1), basename))

            for x in range(n_evs):
                to_set = "1" if (x + 1) == (i + 1) else "0"

                fsf_out.append('set fmri(con_real%i.%i) %s' % (
                    (i + 1), (x + 1), to_set))

                fsf_out.append('set fmri(con_orig%i.%i) %s' % (
                    (i + 1), (x + 1), to_set))

            fsf_out.append('set fmri(ftest_real1.%i) 1' % (i + 1))
            fsf_out.append('set fmri(ftest_orig1.%i) 1' % (i + 1))

        for x in range(n_evs):

            for y in range(n_evs):

                if (x + 1) == (y + 1):
                    continue

                fsf_out.append('set fmri(conmask%i_%i) 0' % ((x + 1),
                                                             (y + 1)))

        return fsf_out


class MelodicCrawler(object):

    def __init__(self, preproc_dir, run_idf, template=None, output_dir=None,
                 subject_idf='sub', func_idf='func', copy_reg=True,
                 copy_mc=True, varnorm=True, n_cores=1):
        """
        Given an fsf-template (Melodic), this crawler creates subject-
        specific fsf-melodic files and (optionally) copies the corresponding
        registration and mc directories to the out-directory.

        Parameters
        ----------
        template : str
            Absolute path to template fsf-file
        preproc_dir : str
            Absolute path to the directory with preprocessed files
        run_idf : str
            Identifier for run to apply template fsf to
        output_dir : str
            Path to desired output dir of Melodic-ica results.
        subject_idf : str
            Identifier for subject-directories.
        func_idf : str
            Identifier for which functional should be use.
        copy_reg : bool
            Whether to copy the subjects' registration directory
        copy_mc : bool
            Whether to copy the subjects' mc directory
        varnorm : bool
            Whether to apply variance-normalization (melodic option)
        n_cores : int
            How many CPU cores should be used for the batch-analysis.
        """

        self.template = template
        self.preproc_dir = preproc_dir
        self.copy_reg = copy_reg
        self.copy_mc = copy_mc

        if output_dir is None:
            output_dir = op.join(op.dirname(template), 'Melodic')

        if not op.isdir(output_dir):
            os.makedirs(output_dir)

        self.output_dir = output_dir
        self.run_idf = run_idf
        self.func_idf = func_idf
        self.subject_idf = subject_idf
        self.varnorm = "1" if varnorm else "0"

        if n_cores == -1:
            n_cores = multiprocessing.cpu_count() - 1

        self.n_cores = n_cores
        self.clean_fsf = None
        self.out_fsf = []
        self.sub_dirs = None

    def crawl(self):
        """ Crawls subject-directories and spits out subject-specific fsf. """
        self._read_fsf()
        run_paths = op.join(self.preproc_dir, '%s*' % self.subject_idf,
                            '*%s*' % self.run_idf)
        self.sub_dirs = sorted(glob(run_paths))
        out_f = [self._write_fsf(sub) for sub in self.sub_dirs]

        shell_script = op.join(op.dirname(self.template), 'batch_melodic.sh')
        with open(shell_script, 'wb') as fout:

            for i, (fsf, reg_cmd, mc_cmd) in enumerate(out_f):

                fout.write('feat %s' % fsf)

                if reg_cmd or mc_cmd:
                    fout.write('\n')
                else:
                    fout.write(' &\n')

                if reg_cmd is not None:
                    fout.write(reg_cmd + ' &\n')

                if mc_cmd is not None:
                    fout.write(mc_cmd + ' &\n')

                if (i+1) % self.n_cores == 0:
                    fout.write('wait\n')

    def _copy_reg(self, sub_dir, ica_dir):
        """ Tries to find reg-dir and returns copy command"""
        dst = op.join(ica_dir, 'reg')
        if op.isdir(dst):
            return None

        regdir = glob(op.join(op.dirname(sub_dir), '*reg*'))
        msg = "Found multiple (or no) registration directories in %s (%r)"

        if len(regdir) != 1:
            warnings.warn(msg % (sub_dir, regdir))
            return None
        else:
            if regdir[0] == 'reg':
                to_copy = regdir[0]
            else:
                regdir_ch = op.join(regdir[0], 'reg')
                if not op.isdir(regdir_ch):
                    warnings.warn(msg % (sub_dir, regdir_ch))
                else:
                    to_copy = regdir_ch

            cmd = 'cp -r %s %s' % (to_copy, dst)
            # print('Copying %s to %s' % (to_copy, dst))
            # shutil.copytree(to_copy, dst)
            return cmd

    def _copy_mc(self, sub_dir, ica_dir):
        """ Tries to find mc-dir and returns copy command."""
        dst = op.join(ica_dir, 'mc')
        if op.isdir(dst):
            return None

        mc_dir = op.join(sub_dir, 'mc')
        if not op.isdir(mc_dir):
            warnings.warn("Could not find mc dir in %s" % sub_dir)
        else:
            # print("Copying %s to %s" % (mc_dir, op.join(ica_dir, 'mc')))
            # shutil.copytree(mc_dir, op.join(ica_dir, 'mc'))
            cmd = 'cp -r %s %s' % (mc_dir, dst)
            return cmd

    def _read_fsf(self):
        """ Reads in template-fsf and does some cleaning. """

        if self.template is None:
            self.template = op.join(op.dirname(skbold.__file__), 'data',
                                    'Melodic_template.fsf')

        with open(self.template, 'rb') as f:
            template = f.readlines()

        template = [txt.replace('\n', '') for txt in template if txt != '\n']
        template = [txt for txt in template if txt[0] != '#']  # remove commnts

        self.clean_fsf = template

    def _write_fsf(self, sub_dir):
        """ Creates and writes out subject-specific fsf. """
        func_file = glob(op.join(sub_dir, '*%s*.nii.gz' % self.func_idf))

        if len(func_file) == 0:
            msg = "Found no func-file for sub %s" % sub_dir
            raise IOError(msg)
        elif len(func_file) > 1:
            msg = "Found more than one func-file for sub %s: %r" % (sub_dir,
                                                                    func_file)
            raise IOError(msg)
        else:
            func_file = func_file[0]

        out_dir = op.join(self.output_dir, op.basename(op.dirname(sub_dir)))
        if not op.isdir(out_dir):
            os.makedirs(out_dir)

        ica_dir = op.join(out_dir, '%s.ica' % self.run_idf)
        hdr = nib.load(func_file).header

        arg_dict = {'tr': hdr['pixdim'][4],
                    'npts': hdr['dim'][4],
                    'feat_files': "\"%s\"" % func_file,
                    'outputdir': "\"%s\"" % ica_dir,
                    'varnorm': self.varnorm,
                    'totalVoxels': np.prod(hdr['dim'][1:5])}

        fsf_out = []
        # Loop over lines in cleaned template-fsf
        for line in self.clean_fsf:

            if any(key in line for key in arg_dict.keys()):
                parts = [txt for txt in line.split(' ') if txt]
                keys = [key for key in arg_dict.keys() if key in line][0]
                values = arg_dict[keys]

                parts[-1] = values
                parts = [str(p) for p in parts]
                fsf_out.append(" ".join(parts))
            else:
                fsf_out.append(line)

        with open(op.join(sub_dir, 'melodic.fsf'), 'wb') as fsfout:
            print("Writing fsf to %s" % sub_dir)
            fsfout.write("\n".join(fsf_out))

        if self.copy_reg:
            reg_cmd = self._copy_reg(sub_dir, ica_dir)

        if self.copy_mc:
            mc_cmd = self._copy_mc(sub_dir, ica_dir)

        return op.join(sub_dir, 'melodic.fsf'), reg_cmd, mc_cmd


if __name__ == '__main__':
    preproc = op.join(op.dirname(op.dirname(op.abspath(__file__))), 'data',
                      'test_data', 'preprocessed')

    output_dir = op.join(op.dirname(preproc), 'firstlevel')

    fsfcrawler = FsfCrawler(preproc_dir=preproc, output_dir=output_dir,
                            run_idf='mytask',
                            template='mvpa',
                            subject_idf='sub', func_idf='func',
                            prewhiten=True, n_cores=1)

    fsfcrawler.crawl()